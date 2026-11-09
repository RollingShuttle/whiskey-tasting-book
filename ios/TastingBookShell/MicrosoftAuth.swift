// MicrosoftAuth.swift — the Microsoft sign-in, done natively so it outlives the day.
//
// In the PWA the page signs in with MSAL inside the browser, and a browser app's refresh token is capped
// at 24 hours — hence the daily sign-in. A native client's refresh token lasts 90 days of inactivity
// (guide/IOS-APP.md §2). So inside the app the page asks this file instead: authorization-code flow with
// PKCE in ASWebAuthenticationSession (the system sheet, which shares Safari's cookies so a repeat sign-in
// is usually one tap), the code exchanged and later refreshed with plain URLSession calls, and the tokens
// kept in the keychain. Nothing but the access token ever reaches the web view, and only as the reply to
// auth.token.
//
// The redirect URI is <redirectScheme>://auth — msauth.com.mikeyang.whiskey://auth in practice — which is
// registered in Entra under "Mobile and desktop applications". It needs no CFBundleURLTypes entry: the
// authentication session captures the callback itself; nothing else on the phone ever sees that URL.
import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

enum SignInOutcome {
    case ok(account: String)
    case failed(error: String, cancelled: Bool)
}

enum TokenOutcome {
    case ok(token: String, expiresAt: Double)
    /// interactive is true when only a sign-in can fix it, false when it is worth trying again later
    /// (no network, a server error).
    case failed(error: String, interactive: Bool)
}

struct AuthStatus {
    let signedIn: Bool
    let account: String?
}

/// One keychain item per client id. expiresAt is milliseconds since the epoch, the way the page keeps time.
private struct StoredTokens: Codable {
    var accessToken: String
    var refreshToken: String
    var expiresAt: Double
    var account: String
}

private struct AuthError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// A non-200 from the token endpoint, with Microsoft's own error code when it sent one.
private struct TokenEndpointError: Error {
    let status: Int
    let code: String?
    let message: String
}

actor MicrosoftAuth {
    static let shared = MicrosoftAuth()

    /// Added to whatever the page asks for: openid and profile buy an id_token to label the account with,
    /// offline_access buys the refresh token that makes the sign-in last.
    private static let baseScopes = ["openid", "profile", "offline_access"]

    /// One refresh in flight per client id. Two uploads can ask for a token in the same instant, and two
    /// concurrent refreshes would race over a rotated refresh token — the loser's would be dead on arrival.
    private var refreshing: [String: Task<TokenOutcome, Never>] = [:]

    // MARK: status

    func status(clientId: String?) -> AuthStatus {
        guard let saved = Keychain.load(clientId: clientId) else { return AuthStatus(signedIn: false, account: nil) }
        return AuthStatus(signedIn: true, account: saved.account)
    }

    // MARK: sign in

    func signIn(clientId: String, authority: String, scopes: [String], redirectScheme: String) async -> SignInOutcome {
        let verifier = PKCE.verifier()
        let challenge = PKCE.challenge(for: verifier)
        let state = PKCE.randomURLSafe(bytes: 16)
        let redirectURI = redirectScheme + "://auth"
        let scope = Self.scopeString(scopes)

        guard var parts = URLComponents(string: authority + "/oauth2/v2.0/authorize") else {
            return .failed(error: "bad authority: \(authority)", cancelled: false)
        }
        parts.queryItems = [
            URLQueryItem(name: "client_id", value: clientId),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "response_mode", value: "query"),
            URLQueryItem(name: "scope", value: scope),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            // Always offer the account picker. The session shares Safari's cookies, so without this
            // a sign-out followed by a sign-in would silently re-enter the same account.
            URLQueryItem(name: "prompt", value: "select_account"),
        ]
        guard let url = parts.url else {
            return .failed(error: "could not build the sign-in address", cancelled: false)
        }

        let callback: URL
        do {
            callback = try await WebAuthRunner().run(url: url, callbackScheme: redirectScheme)
        } catch let e as ASWebAuthenticationSessionError where e.code == .canceledLogin {
            return .failed(error: "sign-in cancelled", cancelled: true)
        } catch {
            return .failed(error: error.localizedDescription, cancelled: false)
        }

        let query = Self.queryItems(of: callback)
        if let err = query["error"] {
            return .failed(error: query["error_description"] ?? err, cancelled: false)
        }
        // The state is minted per attempt and must come back unchanged, or the code belongs to some
        // other request and is not exchanged.
        guard query["state"] == state else {
            return .failed(error: "the sign-in reply did not match this request", cancelled: false)
        }
        guard let code = query["code"], !code.isEmpty else {
            return .failed(error: "the sign-in reply carried no code", cancelled: false)
        }

        do {
            let tokens = try await Self.exchange(authority: authority, form: [
                "client_id": clientId,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirectURI,
                "code_verifier": verifier,
                "scope": scope,
            ], previous: nil)
            Keychain.save(tokens, clientId: clientId)
            return .ok(account: tokens.account)
        } catch let e as TokenEndpointError {
            return .failed(error: e.message, cancelled: false)
        } catch {
            return .failed(error: error.localizedDescription, cancelled: false)
        }
    }

    // MARK: tokens

    /// The cached access token while more than a minute of it remains; otherwise a refresh. A minute,
    /// because the token has to survive the upload it is about to be used for.
    func token(clientId: String, authority: String, scopes: [String]) async -> TokenOutcome {
        if let saved = Keychain.load(clientId: clientId), saved.expiresAt - Self.nowMs() > 60_000 {
            return .ok(token: saved.accessToken, expiresAt: saved.expiresAt)
        }
        if let inFlight = refreshing[clientId] {
            return await inFlight.value
        }
        let task = Task { await self.refresh(clientId: clientId, authority: authority, scopes: scopes) }
        refreshing[clientId] = task
        let outcome = await task.value
        refreshing[clientId] = nil
        return outcome
    }

    private func refresh(clientId: String, authority: String, scopes: [String]) async -> TokenOutcome {
        guard let saved = Keychain.load(clientId: clientId), !saved.refreshToken.isEmpty else {
            return .failed(error: "not signed in", interactive: true)
        }
        do {
            let tokens = try await Self.exchange(authority: authority, form: [
                "client_id": clientId,
                "grant_type": "refresh_token",
                "refresh_token": saved.refreshToken,
                "scope": Self.scopeString(scopes),
            ], previous: saved)
            Keychain.save(tokens, clientId: clientId) // keeps the rotated refresh token
            return .ok(token: tokens.accessToken, expiresAt: tokens.expiresAt)
        } catch let e as TokenEndpointError {
            // invalid_grant: the refresh token expired or was revoked. The other three are Microsoft wanting
            // the user back (a password change, a consent change, a signed-out session). Only a sign-in
            // fixes any of them, so the dead tokens go now rather than being retried for ever, and the
            // page is told to ask for one.
            let needsPerson = ["invalid_grant", "interaction_required", "consent_required", "login_required"]
            if e.status == 400, needsPerson.contains(where: { $0 == e.code }) {
                Keychain.delete(clientId: clientId)
                return .failed(error: e.message, interactive: true)
            }
            return .failed(error: e.message, interactive: false)
        } catch {
            // URLSession errors: offline, DNS, a timeout. The tokens are fine; the bar wifi is not.
            return .failed(error: error.localizedDescription, interactive: false)
        }
    }

    // MARK: sign out

    /// Deletes the stored tokens. With no client id, everything under the service goes, which is what
    /// "sign out" means to the person tapping it.
    func signOut(clientId: String?) {
        Keychain.delete(clientId: clientId)
    }

    // MARK: the token endpoint

    /// POSTs a form to the token endpoint and reads the reply into StoredTokens. `previous` supplies the
    /// refresh token and account label when a refresh reply leaves them out.
    private static func exchange(authority: String, form: [String: String], previous: StoredTokens?) async throws -> StoredTokens {
        guard let url = URL(string: authority + "/oauth2/v2.0/token") else { throw AuthError("bad authority: \(authority)") }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = Data(formEncode(form).utf8)

        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]

        guard status == 200 else {
            let code = json["error"] as? String
            // error_description runs to several lines of trace ids; the first line is the sentence.
            let description = (json["error_description"] as? String)?.components(separatedBy: .newlines).first
            throw TokenEndpointError(status: status, code: code, message: description ?? code ?? "HTTP \(status) from the token endpoint")
        }
        guard let access = json["access_token"] as? String, !access.isEmpty else {
            throw AuthError("the token reply carried no access token")
        }
        let expiresIn = (json["expires_in"] as? Double) ?? Double(json["expires_in"] as? String ?? "") ?? 3600
        let refresh = (json["refresh_token"] as? String) ?? previous?.refreshToken ?? ""
        let account = accountLabel(fromIdToken: json["id_token"] as? String) ?? previous?.account ?? ""
        return StoredTokens(accessToken: access, refreshToken: refresh, expiresAt: nowMs() + expiresIn * 1000, account: account)
    }

    /// The page's scopes behind the base ones, each once, space-separated as the protocol wants them.
    private static func scopeString(_ scopes: [String]) -> String {
        var seen = Set<String>()
        return (baseScopes + scopes).filter { seen.insert($0).inserted }.joined(separator: " ")
    }

    private static func nowMs() -> Double { Date().timeIntervalSince1970 * 1000 }

    private static func queryItems(of url: URL) -> [String: String] {
        var out: [String: String] = [:]
        for item in URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? [] {
            out[item.name] = item.value ?? ""
        }
        return out
    }

    /// application/x-www-form-urlencoded, encoding everything outside the unreserved set (RFC 3986 §2.3).
    private static func formEncode(_ fields: [String: String]) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return fields.map { key, value in
            "\(key)=\(value.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")"
        }.joined(separator: "&")
    }

    /// The id_token is a JWT whose middle part is base64url JSON. Nothing here checks the signature: the
    /// token came straight from Microsoft over TLS and is used for a display label, never for access.
    private static func accountLabel(fromIdToken token: String?) -> String? {
        guard let token else { return nil }
        let parts = token.split(separator: ".")
        guard parts.count >= 2, let data = PKCE.base64URLDecode(String(parts[1])),
              let claims = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        return (claims["preferred_username"] as? String) ?? (claims["email"] as? String)
    }
}

// MARK: - PKCE (RFC 7636)

private enum PKCE {
    /// 32 random bytes, base64url without padding: 43 characters, inside the 43–128 the RFC allows.
    static func verifier() -> String { randomURLSafe(bytes: 32) }

    /// S256: base64url(SHA256(verifier)).
    static func challenge(for verifier: String) -> String {
        base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
    }

    static func randomURLSafe(bytes count: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: count)
        if SecRandomCopyBytes(kSecRandomDefault, count, &bytes) != errSecSuccess {
            bytes = (0..<count).map { _ in UInt8.random(in: 0...255) } // also cryptographically secure on Apple platforms
        }
        return base64URL(Data(bytes))
    }

    static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    /// The inverse, padding back to a multiple of four first because Data(base64Encoded:) insists on it.
    static func base64URLDecode(_ s: String) -> Data? {
        var b64 = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b64.count % 4 != 0 { b64 += "=" }
        return Data(base64Encoded: b64)
    }
}

// MARK: - the keychain

/// One kSecClassGenericPassword item per client id under one service, readable after the first unlock
/// so a refresh works whenever the phone has been unlocked once since it booted.
private enum Keychain {
    static let service = "com.mikeyang.whiskey.auth"

    private static func query(clientId: String?) -> [String: Any] {
        var q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        if let clientId { q[kSecAttrAccount as String] = clientId }
        return q
    }

    static func load(clientId: String?) -> StoredTokens? {
        var q = query(clientId: clientId)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(StoredTokens.self, from: data)
    }

    static func save(_ tokens: StoredTokens, clientId: String) {
        guard let data = try? JSONEncoder().encode(tokens) else { return }
        let q = query(clientId: clientId)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let status = SecItemUpdate(q as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            _ = SecItemAdd(q.merging(attributes) { _, new in new } as CFDictionary, nil)
        }
    }

    static func delete(clientId: String?) {
        _ = SecItemDelete(query(clientId: clientId) as CFDictionary)
    }
}

// MARK: - the system sign-in sheet

/// Runs one ASWebAuthenticationSession and hands back the callback URL. The session's completion handler
/// holds the runner, and the runner holds the session, until the sheet is dismissed one way or the other.
private final class WebAuthRunner: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    func run(url: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
            DispatchQueue.main.async {
                let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callbackScheme) { callback, error in
                    self.session = nil
                    if let callback {
                        continuation.resume(returning: callback)
                    } else {
                        continuation.resume(throwing: error ?? AuthError("the sign-in sheet closed without a result"))
                    }
                }
                session.presentationContextProvider = self
                session.prefersEphemeralWebBrowserSession = false // share Safari's Microsoft cookies: a repeat sign-in is one tap
                self.session = session
                if !session.start() {
                    self.session = nil
                    continuation.resume(throwing: AuthError("could not open the sign-in sheet"))
                }
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        return scenes.flatMap(\.windows).first(where: \.isKeyWindow) ?? scenes.first?.windows.first ?? ASPresentationAnchor()
    }
}

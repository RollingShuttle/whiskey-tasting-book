// Bridge.swift — the message router between the page and the native shell.
//
//   web → native:  window.webkit.messageHandlers.native.postMessage({ type, ...fields })
//   native → web:  window.nativeEvent({ type, ...fields })
//
// Every request the page sends carries a string `id` it minted, and the reply echoes it, so the page can
// match answers to questions however many are in flight. The page's half of this is docs/native.js.
//
//   ready                                          → hello {version, caps}
//   auth.status {id}                               → auth.status.result {id, signedIn, account}
//   auth.signIn {id, clientId, authority, scopes, redirectScheme}
//                                                  → auth.result {id, ok:true, account}
//                                                    | auth.result {id, ok:false, error, cancelled}
//   auth.token {id, clientId, authority, scopes}   → auth.token.result {id, ok:true, token, expiresAt}
//                                                    | auth.token.result {id, ok:false, error, interactive}
//   auth.signOut {id}                              → auth.signOut.result {id, ok:true}
//   backup.save {name, json}                       (no reply)
//   backup.load {id}                               → backup.result {id, none:true}
//                                                    | backup.result {id, name, text, savedAt}
//   (unsolicited)                                  ← foreground, when the app comes back to the front
//
// The client id, authority and scopes ride along in every auth message rather than being compiled in:
// docs/config.js is the one place they live, for the PWA and the app alike.
import UIKit
import WebKit

final class Bridge: NSObject, WKScriptMessageHandler {
    static let shared = Bridge()
    static let version = "1.0"
    static let caps = ["auth", "backup"]

    private weak var webView: WKWebView?

    func attach(_ webView: WKWebView) {
        self.webView = webView
    }

    // MARK: web → native

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "native", let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        // Pulled out here so the Tasks below capture plain strings, not the message dictionary.
        let id = body["id"] as? String
        let clientId = body["clientId"] as? String
        let authority = body["authority"] as? String
        let scopes = body["scopes"] as? [String] ?? []
        let redirectScheme = body["redirectScheme"] as? String

        switch type {
        case "ready":
            send(["type": "hello", "version": Self.version, "caps": Self.caps])

        case "auth.status":
            Task { [weak self] in
                let status = await MicrosoftAuth.shared.status(clientId: clientId)
                let account: Any = status.account.map { $0 as Any } ?? NSNull()
                self?.reply(id, ["type": "auth.status.result", "signedIn": status.signedIn, "account": account])
            }

        case "auth.signIn":
            guard let clientId, let authority, let redirectScheme else {
                reply(id, ["type": "auth.result", "ok": false, "error": "auth.signIn needs clientId, authority and redirectScheme", "cancelled": false])
                return
            }
            Task { [weak self] in
                switch await MicrosoftAuth.shared.signIn(clientId: clientId, authority: authority, scopes: scopes, redirectScheme: redirectScheme) {
                case .ok(let account):
                    self?.reply(id, ["type": "auth.result", "ok": true, "account": account])
                case .failed(let error, let cancelled):
                    self?.reply(id, ["type": "auth.result", "ok": false, "error": error, "cancelled": cancelled])
                }
            }

        case "auth.token":
            guard let clientId, let authority else {
                reply(id, ["type": "auth.token.result", "ok": false, "error": "auth.token needs clientId and authority", "interactive": false])
                return
            }
            Task { [weak self] in
                switch await MicrosoftAuth.shared.token(clientId: clientId, authority: authority, scopes: scopes) {
                case .ok(let token, let expiresAt):
                    self?.reply(id, ["type": "auth.token.result", "ok": true, "token": token, "expiresAt": expiresAt])
                case .failed(let error, let interactive):
                    self?.reply(id, ["type": "auth.token.result", "ok": false, "error": error, "interactive": interactive])
                }
            }

        case "auth.signOut":
            Task { [weak self] in
                await MicrosoftAuth.shared.signOut(clientId: clientId)
                self?.reply(id, ["type": "auth.signOut.result", "ok": true])
            }

        case "backup.save":
            guard let name = body["name"] as? String, let json = body["json"] as? String else { return }
            // Off the main thread: the whole store arrives about once a second while a flight is being
            // scored, and an atomic write of a few hundred KB is a visible hitch in a web view mid-scroll.
            DispatchQueue.global(qos: .utility).async { BackupStore.save(name: name, json: json) }

        case "backup.load":
            if let b = BackupStore.latest() {
                reply(id, ["type": "backup.result", "name": b.name, "text": b.text, "savedAt": b.savedAtMs])
            } else {
                reply(id, ["type": "backup.result", "none": true])
            }

        default:
            break
        }
    }

    // MARK: native → web

    /// Hands an event to the page: JSON-encoded and evaluated as a JavaScript literal on the main thread,
    /// which is the only thread WKWebView will be spoken to from.
    func send(_ event: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: event), let json = String(data: data, encoding: .utf8) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript("window.nativeEvent(\(json))", completionHandler: nil)
        }
    }

    /// A reply to a request: send(), with the request's id echoed when the page gave one.
    private func reply(_ id: String?, _ fields: [String: Any]) {
        var event = fields
        if let id { event["id"] = id }
        send(event)
    }
}

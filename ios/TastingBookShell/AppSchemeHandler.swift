// AppSchemeHandler.swift — serves the bundle's web/ folder as if it were a static web server:
// app://local/index.html → web/index.html.
//
// A custom scheme rather than file:// because localStorage needs a stable origin to persist under, and
// because fetch() of a sibling file (rubric.json) is refused from file://. The scheme is registered with
// WKWebView in WebView.swift; nothing outside the app can address it.
import WebKit
import UniformTypeIdentifiers

final class AppSchemeHandler: NSObject, WKURLSchemeHandler {
    static let scheme = "app"
    static let host = "local"
    static let indexURL = URL(string: "\(scheme)://\(host)/index.html")!

    private static let webRoot: URL? = Bundle.main.resourceURL?.appendingPathComponent("web", isDirectory: true)

    func webView(_ webView: WKWebView, start urlSchemeTask: WKURLSchemeTask) {
        guard let url = urlSchemeTask.request.url else {
            urlSchemeTask.didFailWithError(URLError(.badURL))
            return
        }
        var path = url.path
        if path.isEmpty || path == "/" { path = "/index.html" }
        guard let root = Self.webRoot else {
            urlSchemeTask.didFailWithError(URLError(.fileDoesNotExist))
            return
        }
        let fileURL = root.appendingPathComponent(String(path.dropFirst())).standardizedFileURL
        // A "../" in the path must not read anything outside web/.
        guard fileURL.path.hasPrefix(root.standardizedFileURL.path), let data = FileManager.default.contents(atPath: fileURL.path) else {
            let response = HTTPURLResponse(url: url, statusCode: 404, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "text/plain"])!
            urlSchemeTask.didReceive(response)
            urlSchemeTask.didReceive(Data("not found: \(path)".utf8))
            urlSchemeTask.didFinish()
            return
        }
        let headers = [
            "Content-Type": Self.mimeType(for: fileURL.pathExtension),
            "Content-Length": String(data.count),
            "Cache-Control": "no-cache",
        ]
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: headers)!
        urlSchemeTask.didReceive(response)
        urlSchemeTask.didReceive(data)
        urlSchemeTask.didFinish()
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {
        // Every file is read and answered synchronously in start(); there is nothing left to cancel.
    }

    static func mimeType(for ext: String) -> String {
        switch ext.lowercased() {
        case "html": return "text/html; charset=utf-8"
        case "js", "mjs": return "text/javascript; charset=utf-8"
        case "css": return "text/css; charset=utf-8"
        case "json": return "application/json; charset=utf-8"
        case "webmanifest": return "application/manifest+json"
        case "png": return "image/png"
        case "jpg", "jpeg": return "image/jpeg"
        case "svg": return "image/svg+xml"
        case "ico": return "image/x-icon"
        case "woff2": return "font/woff2"
        case "txt": return "text/plain; charset=utf-8"
        default:
            if let type = UTType(filenameExtension: ext), let mime = type.preferredMIMEType { return mime }
            return "application/octet-stream"
        }
    }
}

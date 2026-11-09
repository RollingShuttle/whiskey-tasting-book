// WebView.swift — the SwiftUI wrapper around WKWebView: loads the bundled web/ folder through the
// app:// scheme, turns JavaScript alert / confirm / prompt into native sheets, and hands any http(s)
// link to an in-app Safari view rather than letting the shell navigate away from the page.
import SwiftUI
import WebKit
import SafariServices

struct WebContainerView: UIViewRepresentable {
    /// #12100E — the page's background (docs/style.css, SPEC.md §5). The web view shows it before the
    /// first paint and behind any overscroll, so the app is never white, not even for a frame.
    static let pageBackground = UIColor(red: 18.0 / 255.0, green: 16.0 / 255.0, blue: 14.0 / 255.0, alpha: 1)

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.setURLSchemeHandler(AppSchemeHandler(), forURLScheme: AppSchemeHandler.scheme)
        config.userContentController.add(Bridge.shared, name: "native")

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.scrollView.contentInsetAdjustmentBehavior = .never // the page handles the safe areas itself
        webView.scrollView.bounces = true
        webView.isOpaque = false
        webView.backgroundColor = Self.pageBackground
        webView.scrollView.backgroundColor = Self.pageBackground
        webView.allowsBackForwardNavigationGestures = false // the page has its own back chevron (SPEC.md §9.2)
        #if DEBUG
        if #available(iOS 16.4, *) {
            webView.isInspectable = true // Safari on the Mac → Develop → the iPhone → the page's console
        }
        #endif

        Bridge.shared.attach(webView)
        webView.load(URLRequest(url: AppSchemeHandler.indexURL))
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        // Only the app:// scheme may navigate the web view. The Microsoft sign-in never comes through
        // here — it runs in ASWebAuthenticationSession — so an http(s) navigation can only be a link the
        // page shows, and it opens in Safari's own view controller on top of the app.
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else { return decisionHandler(.allow) }
            if url.scheme == AppSchemeHandler.scheme || url.scheme == "about" {
                return decisionHandler(.allow)
            }
            if url.scheme == "http" || url.scheme == "https" {
                TopViewController.present(SFSafariViewController(url: url))
            }
            decisionHandler(.cancel)
        }

        // window.alert / window.confirm / window.prompt — the page uses confirm for Discard and Delete.
        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            TopViewController.present(alert)
        }

        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            TopViewController.present(alert)
        }

        func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
            let alert = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
            alert.addTextField { $0.text = defaultText }
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(nil) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(alert.textFields?.first?.text) })
            TopViewController.present(alert)
        }

        // iOS reclaimed the web content process (memory pressure): reload. The page restores its draft
        // from localStorage on load, so nothing in progress is lost.
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            webView.load(URLRequest(url: AppSchemeHandler.indexURL))
        }
    }
}

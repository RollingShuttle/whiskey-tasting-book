// TastingBookShellApp.swift — the entry point: one full-screen WKWebView carrying the phone client
// (docs/, the same files GitHub Pages serves) from inside the app bundle. The native side does only
// what a web page cannot: a Microsoft sign-in that outlives the day, and a backup file the Files app
// can see. Everything else — scoring, the queue, the offline cache — is the page, unchanged.
import SwiftUI

@main
struct TastingBookShellApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            WebContainerView()
                .ignoresSafeArea() // the page lays out its own safe-area insets (viewport-fit=cover, SPEC.md §9.2)
                .background(Color(red: 0.0706, green: 0.0627, blue: 0.0549)) // #12100E, the page's own background, so a slow first paint never flashes white
        }
    }
}

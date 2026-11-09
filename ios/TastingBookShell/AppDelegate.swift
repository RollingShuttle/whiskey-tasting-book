// AppDelegate.swift — one job: tell the page when the app comes back to the foreground, so it can
// pull what the PC published (throttled; docs/sync.js MIN_INTERVAL), retry its upload queue and
// re-read the clock. No notifications, no intents; this app has nothing to say while it is not open.
import UIKit

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        NotificationCenter.default.addObserver(forName: UIApplication.willEnterForegroundNotification, object: nil, queue: .main) { _ in
            Bridge.shared.send(["type": "foreground"])
        }
        return true
    }
}

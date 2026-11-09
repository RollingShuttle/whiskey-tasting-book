// NativeHelpers.swift — finding the top-most view controller, which is where alerts, the Safari view
// and anything else presented over the web view have to go. SwiftUI owns the window; this looks it up.
import UIKit

enum TopViewController {
    static func current() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let window = scenes.flatMap(\.windows).first(where: \.isKeyWindow) ?? scenes.first?.windows.first
        var top = window?.rootViewController
        while let presented = top?.presentedViewController { top = presented }
        return top
    }

    static func present(_ vc: UIViewController) {
        DispatchQueue.main.async {
            guard let top = current() else { return }
            if let pop = vc.popoverPresentationController { // a popover needs an anchor or UIKit asserts
                pop.sourceView = top.view
                pop.sourceRect = CGRect(x: top.view.bounds.midX, y: top.view.bounds.maxY - 80, width: 1, height: 1)
            }
            top.present(vc, animated: true)
        }
    }
}

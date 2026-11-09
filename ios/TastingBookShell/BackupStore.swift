// BackupStore.swift — the backup file. The page hands over its whole local state as JSON (backup.save)
// after a change, and this writes it into the app's own Documents folder. UIFileSharingEnabled and
// LSSupportsOpeningDocumentsInPlace are on in the project, so the file shows up in the Files app under
// On My iPhone → Tasting Book, where it can be copied off by hand. On a cold start the page may ask for
// the newest one back (backup.load → backup.result) if iOS has reclaimed its web storage.
import Foundation

enum BackupStore {
    static var directory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    @discardableResult
    static func save(name: String, json: String) -> Bool {
        let safeName = (name as NSString).lastPathComponent // the page names the file, never the folder
        guard !safeName.isEmpty, safeName != ".", safeName != ".." else { return false }
        let url = directory.appendingPathComponent(safeName)
        do {
            try json.write(to: url, atomically: true, encoding: .utf8)
            return true
        } catch {
            return false
        }
    }

    /// The most recently changed .json in the folder — the owner may also drop an exported file in there.
    static func latest() -> (name: String, text: String, savedAtMs: Double)? {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]) else {
            return nil
        }
        let dated: [(url: URL, date: Date)] = items
            .filter { $0.pathExtension.lowercased() == "json" }
            .map { ($0, (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast) }
        guard let newest = dated.max(by: { $0.date < $1.date }), let text = try? String(contentsOf: newest.url, encoding: .utf8) else {
            return nil
        }
        return (newest.url.lastPathComponent, text, newest.date.timeIntervalSince1970 * 1000)
    }
}

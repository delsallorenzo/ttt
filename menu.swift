import AppKit
import SQLite3

let home = NSHomeDirectory()
let dbPath = home + "/.usage/usage.db"

func fmtTotal(_ secs: Int) -> String {
    let m = Int(Double(secs) / 60.0 + 0.5)
    if m >= 60 { return "\(m / 60)h \(m % 60)min" }
    return "\(m)min"
}

func todayTotal() -> String {
    var db: OpaquePointer?
    guard sqlite3_open(dbPath, &db) == SQLITE_OK, let db = db else { return "..." }
    defer { sqlite3_close(db) }
    let midnight = Calendar.current.startOfDay(for: Date()).timeIntervalSince1970
    let sql = "SELECT COUNT(*) FROM samples WHERE ts>=? AND is_idle=0"
    var stmt: OpaquePointer?
    guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt = stmt else { return "..." }
    defer { sqlite3_finalize(stmt) }
    sqlite3_bind_double(stmt, 1, midnight)
    var n: Int32 = 0
    if sqlite3_step(stmt) == SQLITE_ROW { n = sqlite3_column_int(stmt, 0) }
    return fmtTotal(Int(n) * 5)
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.target = self
        item.button?.action = #selector(openLive)
        update()
        Timer.scheduledTimer(withTimeInterval: 30.0, repeats: true) { _ in self.update() }
    }
    func update() { item.button?.title = todayTotal() }
    @objc func openLive() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", "tell application \"Terminal\" to do script \"\(home)/.local/bin/ttt\"",
                       "-e", "tell application \"Terminal\" to activate"]
        try? p.run()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()

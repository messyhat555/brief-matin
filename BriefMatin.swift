// Brief Matin — petite fenetre qui affiche le brief du jour.
// Au lancement : regenere le HTML, puis l'affiche dans une WKWebView.

import Cocoa
import WebKit

let base = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".local/share/brief-matin")
let htmlURL = base.appendingPathComponent("brief.html")
let script  = base.appendingPathComponent("brief.py")

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!

    func applicationDidFinishLaunching(_ note: Notification) {
        let w = CGFloat(ProcessInfo.processInfo.environment["BRIEF_W"]
            .flatMap(Double.init) ?? 520)
        let h = CGFloat(ProcessInfo.processInfo.environment["BRIEF_H"]
            .flatMap(Double.init) ?? 780)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: w, height: h),
            styleMask: [.titled, .closable, .miniaturizable, .resizable,
                        .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "Brief du matin"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.center()
        window.setFrameAutosaveName("BriefMatin")

        let cfg = WKWebViewConfiguration()
        web = WKWebView(frame: window.contentView!.bounds, configuration: cfg)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        if web.responds(to: Selector(("setDrawsBackground:"))) {
            web.setValue(false, forKey: "drawsBackground")
        }
        window.contentView!.addSubview(web)

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        refresh()
    }

    /// Regenere le brief puis le charge. Si la generation echoue, on affiche
    /// quand meme la derniere version connue.
    func refresh() {
        DispatchQueue.global(qos: .userInitiated).async {
            if FileManager.default.fileExists(atPath: script.path) {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                p.arguments = ["python3", script.path, "render"]
                p.standardOutput = Pipe(); p.standardError = Pipe()
                try? p.run()
                p.waitUntilExit()
            }
            DispatchQueue.main.async { self.load() }
        }
    }

    func load() {
        if FileManager.default.fileExists(atPath: htmlURL.path) {
            web.loadFileURL(htmlURL, allowingReadAccessTo: base)
        } else {
            web.loadHTMLString(
                "<body style='font:14px -apple-system;padding:28px;color:#888'>"
                + "Aucun brief à afficher.<br><br>Lance <code>brief-matin doctor</code>"
                + " dans un terminal.</body>", baseURL: nil)
        }
    }

    // Cmd-R recharge, Cmd-W ferme
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool {
        return true
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate

let menu = NSMenu()
let item = NSMenuItem()
menu.addItem(item)
let sub = NSMenu()
sub.addItem(withTitle: "Rafraîchir", action: #selector(NSWindow.close), keyEquivalent: "r")
sub.addItem(withTitle: "Fermer", action: #selector(NSWindow.performClose(_:)),
            keyEquivalent: "w")
sub.addItem(withTitle: "Quitter", action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q")
item.submenu = sub
app.mainMenu = menu

app.run()

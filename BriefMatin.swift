// Brief Matin — petite fenetre qui affiche le brief du jour.
// Au lancement : regenere le HTML, puis l'affiche dans une WKWebView.

import Cocoa
import WebKit
import UserNotifications

let base = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".local/share/brief-matin")
let htmlURL = base.appendingPathComponent("brief.html")
let script  = base.appendingPathComponent("brief.py")

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate,
                         WKScriptMessageHandler {
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
        // pont page -> app : la page demande de cocher, l'app ecrit dans la note
        cfg.userContentController.add(self, name: "brief")
        web = WKWebView(frame: window.contentView!.bounds, configuration: cfg)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        if web.responds(to: Selector(("setDrawsBackground:"))) {
            web.setValue(false, forKey: "drawsBackground")
        }
        window.contentView!.addSubview(web)

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        // c'est ici, fenetre au premier plan, que macOS peut poser la question
        // des notifications ; le mode --notifier la trouvera deja repondue
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound]) { _, _ in }

        refresh()
    }

    /// Recoit les demandes de la page (cocher un devoir).
    func userContentController(_ c: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let corps = message.body as? [String: Any],
              corps["action"] as? String == "cocher",
              let fichier = corps["fichier"] as? String,
              let ligne = corps["ligne"] as? Int, ligne > 0 else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", script.path, "cocher",
                           "--fichier", fichier, "--ligne", String(ligne),
                           "--etat", "fait"]
            p.standardOutput = Pipe(); p.standardError = Pipe()
            try? p.run()
            p.waitUntilExit()
        }
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

    @objc func rafraichir() { refresh() }

    // Cmd-W ferme
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool {
        return true
    }
}

// --- mode notification : pas de fenetre, on poste et on sort ---------------
// Usage : BriefMatin --notifier "titre" "sous-titre" "corps"
let args = CommandLine.arguments
if args.count >= 3, args[1] == "--notifier" {
    let titre = args[2]
    let sousTitre = args.count > 3 ? args[3] : ""
    let corps = args.count > 4 ? args[4] : ""

    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)

    let centre = UNUserNotificationCenter.current()
    var fini = false
    var souci: String? = nil

    centre.requestAuthorization(options: [.alert, .sound]) { accorde, err in
        if let err = err {
            souci = "ERREUR autorisation : \(err.localizedDescription)"
            fini = true; return
        }
        guard accorde else {
            souci = "ERREUR notifications refusées pour Brief Matin"
            fini = true; return
        }
        let contenu = UNMutableNotificationContent()
        contenu.title = titre
        if !sousTitre.isEmpty { contenu.subtitle = sousTitre }
        contenu.body = corps
        contenu.sound = .default
        let requete = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: contenu, trigger: nil)
        centre.add(requete) { err in
            if let err = err { souci = "ERREUR envoi : \(err.localizedDescription)" }
            fini = true
        }
    }

    let limite = Date().addingTimeInterval(10)
    while !fini && Date() < limite {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    if let souci = souci { print(souci); exit(1) }
    if !fini { print("ERREUR délai dépassé"); exit(1) }
    // laisse le temps au systeme de prendre la notification
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.4))
    exit(0)
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate

let menu = NSMenu()
let item = NSMenuItem()
menu.addItem(item)
let sub = NSMenu()
sub.addItem(withTitle: "Rafraîchir", action: #selector(AppDelegate.rafraichir),
            keyEquivalent: "r")
sub.addItem(withTitle: "Fermer", action: #selector(NSWindow.performClose(_:)),
            keyEquivalent: "w")
sub.addItem(withTitle: "Quitter", action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q")
item.submenu = sub
app.mainMenu = menu

app.run()

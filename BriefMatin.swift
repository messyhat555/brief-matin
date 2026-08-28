// Brief Matin — petite fenetre qui affiche le brief du jour.
// Au lancement : regenere le HTML, puis l'affiche dans une WKWebView.

import Cocoa
import WebKit
import UserNotifications
import Security

let base = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".local/share/brief-matin")
let htmlURL = base.appendingPathComponent("brief.html")
let script  = base.appendingPathComponent("brief.py")

// --- trousseau ------------------------------------------------------------
// Le jeton vit dans le trousseau plutot qu'en clair dans un fichier : l'element
// y est chiffre au repos et rattache a cette application.
enum Trousseau {
    static let service = "com.briefmatin.zeus"
    static let compte = "token"

    static func ecrire(_ valeur: String) -> Bool {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                   kSecAttrService as String: service,
                                   kSecAttrAccount as String: compte]
        SecItemDelete(base as CFDictionary)
        var ajout = base
        ajout[kSecValueData as String] = valeur.data(using: .utf8)!
        ajout[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
        return SecItemAdd(ajout as CFDictionary, nil) == errSecSuccess
    }

    static func lire() -> String? {
        let req: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrService as String: service,
                                  kSecAttrAccount as String: compte,
                                  kSecReturnData as String: true,
                                  kSecMatchLimit as String: kSecMatchLimitOne]
        var sortie: CFTypeRef?
        guard SecItemCopyMatching(req as CFDictionary, &sortie) == errSecSuccess,
              let d = sortie as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }

    static func effacer() {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                       kSecAttrService as String: service,
                       kSecAttrAccount as String: compte] as CFDictionary)
    }
}

/// Fenetre de connexion a Zeus. L'utilisateur s'y connecte normalement ;
/// des que le site range son jeton, on le recupere et on referme.
final class ConnexionZeus: NSObject, WKNavigationDelegate, NSWindowDelegate {
    private var fenetre: NSWindow!
    private var web: WKWebView!
    private var minuterie: Timer?
    private var fini = false
    private let termine: (String?) -> Void

    /// On n'autorise que Zeus et l'authentification Microsoft. Sans barre
    /// d'adresse, c'est le seul garde-fou contre une redirection hostile.
    private let hotesPermis = [
        "zeus.ionis-it.com", "ionis-it.com",
        "login.microsoftonline.com", "login.microsoft.com", "login.live.com",
        "aadcdn.msftauth.net", "aadcdn.msauth.net", "msftauth.net",
        "msauth.net", "microsoftonline.com", "office.com", "office365.com",
    ]

    private func permis(_ hote: String?) -> Bool {
        guard let h = hote?.lowercased() else { return false }
        return hotesPermis.contains { h == $0 || h.hasSuffix("." + $0) }
    }

    func webView(_ w: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let hote = action.request.url?.host
        if permis(hote) {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)
            let ou = hote ?? "?"
            fenetre?.title = "Navigation bloquée vers \(ou) — connexion interrompue"
            NSLog("[BriefMatin] navigation refusee vers \(ou)")
        }
    }

    func webView(_ w: WKWebView, didCommit navigation: WKNavigation!) {
        // sans barre d'adresse, le titre dit ou on se trouve reellement
        fenetre?.title = "Connexion Zeus — " + (w.url?.host ?? "?")
    }

    init(termine: @escaping (String?) -> Void) {
        self.termine = termine
        super.init()
    }

    func ouvrir(url: String) {
        fenetre = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 980, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        fenetre.title = "Connexion Zeus — chargement…"
        fenetre.center()
        fenetre.delegate = self

        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()     // la session doit persister
        web = WKWebView(frame: fenetre.contentView!.bounds, configuration: cfg)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        // certains fournisseurs d'identite refusent les vues embarquees ;
        // on se presente comme un Safari de bureau
        web.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        fenetre.contentView!.addSubview(web)
        fenetre.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        web.load(URLRequest(url: URL(string: url)!))

        minuterie = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.chercherJeton()
        }
        // si rien au bout de cinq minutes, on abandonne proprement
        Timer.scheduledTimer(withTimeInterval: 300, repeats: false) { [weak self] _ in
            self?.conclure(nil)
        }
    }

    private func chercherJeton() {
        let js = """
        (() => { const b = localStorage.getItem('ZEUS-AUTH');
          if (!b) return null;
          try { const o = JSON.parse(b); return (o && o.token) ? o.token : null; }
          catch (e) { return null; } })()
        """
        web.evaluateJavaScript(js) { [weak self] valeur, _ in
            if let jeton = valeur as? String, jeton.count > 40 {
                self?.conclure(jeton)
            }
        }
    }

    private func conclure(_ jeton: String?) {
        guard !fini else { return }
        fini = true
        minuterie?.invalidate()
        fenetre?.close()
        termine(jeton)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { conclure(nil); return true }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate,
                         WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!

    /// true quand l'app est lancee uniquement pour la connexion Zeus
    var connexionSeule = false

    func applicationDidFinishLaunching(_ note: Notification) {
        if CommandLine.arguments.contains("--connexion") {
            connexionSeule = true
            connecterZeus()
            return
        }
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

    var connexion: ConnexionZeus?

    /// Ouvre la connexion Zeus, enregistre le jeton, puis rafraichit le brief.
    func connecterZeus() {
        connexion = ConnexionZeus { [weak self] jeton in
            guard let jeton = jeton else {
                if self?.connexionSeule == true {
                    print("aucun jeton récupéré"); exit(1)
                }
                return
            }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", script.path, "zeus-enregistrer"]
            let entree = Pipe()
            p.standardInput = entree
            p.standardOutput = Pipe(); p.standardError = Pipe()
            try? p.run()
            entree.fileHandleForWriting.write(jeton.data(using: .utf8)!)
            entree.fileHandleForWriting.closeFile()
            p.waitUntilExit()
            if self?.connexionSeule == true {
                print("jeton récupéré et enregistré"); exit(0)
            }
            DispatchQueue.main.async { self?.refresh() }
        }
        connexion?.ouvrir(url: "https://zeus.ionis-it.com/home")
    }

    /// Recoit les demandes de la page (cocher un devoir, se connecter).
    func userContentController(_ c: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let corps = message.body as? [String: Any] else { return }
        if corps["action"] as? String == "connecter" {
            DispatchQueue.main.async { self.connecterZeus() }
            return
        }
        guard corps["action"] as? String == "cocher",
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
    @objc func menuConnecter() { connecterZeus() }

    // Cmd-W ferme
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool {
        return true
    }
}

// --- modes trousseau -------------------------------------------------------
if CommandLine.arguments.count >= 2 {
    switch CommandLine.arguments[1] {
    case "--keychain-set":
        let valeur = String(data: FileHandle.standardInput.readDataToEndOfFile(),
                            encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        exit(valeur.isEmpty || !Trousseau.ecrire(valeur) ? 1 : 0)
    case "--keychain-get":
        guard let v = Trousseau.lire() else { exit(1) }
        print(v); exit(0)
    case "--keychain-clear":
        Trousseau.effacer(); exit(0)
    default: break
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
sub.addItem(withTitle: "Se connecter à Zeus…",
            action: #selector(AppDelegate.menuConnecter), keyEquivalent: "l")
sub.addItem(withTitle: "Fermer", action: #selector(NSWindow.performClose(_:)),
            keyEquivalent: "w")
sub.addItem(withTitle: "Quitter", action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q")
item.submenu = sub
app.mainMenu = menu

app.run()

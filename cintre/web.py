"""Serveur web intégré : sert le site vitrine statique (dossier LANDING_DIR).

Contrairement au webhook WhatsApp (mono-thread car il écrit dans `conn`, liée au
thread), ce serveur ne touche PAS la base — il ne sert que des fichiers. On peut
donc utiliser `ThreadingHTTPServer` (une requête par thread) sans violer la règle
« une connexion SQLite par thread ».

Démarré comme un thread daemon depuis app.py, à côté d'ingress/worker/receivers.
Boucle sur `handle_request()` avec un timeout court pour relire `stop` et s'arrêter
proprement au Ctrl+C / SIGTERM.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .logsetup import get_logger

log = get_logger("web")


class _Handler(SimpleHTTPRequestHandler):
    # MIME manquants selon les plateformes ; on les force pour la landing.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".webmanifest": "application/manifest+json",
    }

    def log_message(self, *args) -> None:  # silence le logging par défaut
        pass


class StaticServer:
    """Sert `root` sur (host, port) jusqu'à ce que `stop` soit levé."""

    name = "web"

    def __init__(self, host: str, port: int, root: Path) -> None:
        self.host = host
        self.port = port
        self.root = root

    def run(self, stop: threading.Event) -> None:
        if not (self.root / "index.html").exists():
            log.warning("landing introuvable (%s) : serveur web non démarré", self.root)
            return
        handler = functools.partial(_Handler, directory=str(self.root))
        server = ThreadingHTTPServer((self.host, self.port), handler)
        server.daemon_threads = True
        server.timeout = 1  # handle_request rend la main ~1 s → on relit `stop`
        log.info("site vitrine servi sur http://%s:%d", self.host, self.port)
        try:
            while not stop.is_set():
                server.handle_request()
        finally:
            server.server_close()

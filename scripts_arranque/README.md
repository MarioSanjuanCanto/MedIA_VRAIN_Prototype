# Scripts

Lanzadores locales para Media API y el frontend.

## macOS/Linux

```bash
./scripts/start-media-web.sh
```

Usa `--install` la primera vez si todavía no instalaste las dependencias:

```bash
./scripts/start-media-web.sh --install
```

## Windows

```powershell
.\scripts\start-media-web.ps1
```

Añade `-Install` para instalar antes de arrancar. Ambos lanzadores mantienen
Media API y frontend activos hasta pulsar `Ctrl+C`. Media Core se inicia por
separado desde `services/media-core/`.

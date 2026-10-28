# Puesta en marcha de MedIA en una máquina virtual

## Propósito

Esta es la guía operativa para instalar MedIA en una VM Linux. Presupone que se ha completado la lista de [Preparar MedIA para desplegarlo en la nube](despliegue-en-la-nube.md) y que el repositorio ya contiene Dockerfiles, `compose.yaml` y `.env.cloud.example` probados.

La configuración recomendada para dos colaboradores es una sola VM, una instancia de cada servicio y acceso privado mediante Tailscale.

## 1. Crear la VM

En el proveedor cloud elegido, crear una máquina con:

| Recurso | Valor inicial |
|---|---:|
| Sistema | Ubuntu 24.04 LTS de 64 bits |
| CPU | 4 vCPU |
| Memoria | 12 GB recomendados |
| Disco | 80 GB SSD ampliables |
| Copias | Snapshots automáticos habilitados |
| Acceso | Clave SSH, sin contraseña |

En el firewall del proveedor, permitir inicialmente solo SSH desde una IP conocida. No abrir `3000`, `8001`, `8010` ni `8642`.

Guardar:

- IP pública de la VM.
- Usuario inicial de Ubuntu.
- Clave SSH utilizada.
- Región y tamaño del disco.

## 2. Conectarse y actualizar Ubuntu

```bash
ssh USUARIO@IP_DE_LA_VM
sudo apt update
sudo apt upgrade
sudo reboot
```

Volver a conectar después del reinicio:

```bash
ssh USUARIO@IP_DE_LA_VM
```

Comprobar recursos:

```bash
nproc
free -h
df -h
```

## 3. Instalar Docker Engine y Compose

Seguir siempre la [documentación oficial para Ubuntu](https://docs.docker.com/engine/install/ubuntu/). La secuencia actual es:

```bash
sudo apt update
sudo apt install ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Añadir el repositorio:

```bash
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Permitir Docker al usuario de despliegue:

```bash
sudo usermod -aG docker "$USER"
```

Cerrar y volver a abrir SSH. Después verificar:

```bash
docker version
docker compose version
docker run --rm hello-world
```

El grupo `docker` concede privilegios equivalentes a administrador. No añadir usuarios finales a ese grupo.

## 4. Instalar Tailscale

Instalar Tailscale siguiendo su [guía oficial para Linux](https://tailscale.com/download/linux) y unir la VM a la tailnet:

```bash
sudo tailscale up
tailscale status
```

Abrir la URL de autenticación que aparezca y utilizar la cuenta del equipo. Configurar en Tailscale qué usuarios pueden acceder a esta VM.

## 5. Descargar MedIA

Crear el directorio de instalación:

```bash
sudo install -d -o "$USER" -g "$USER" /opt/media
```

Clonar una versión concreta:

```bash
git clone --branch VERSION_PUBLICADA URL_DEL_REPOSITORIO /opt/media
cd /opt/media
```

Para un repositorio privado, utilizar una deploy key de solo lectura. No guardar tokens personales dentro de la URL Git.

## 6. Configurar secretos y variables

```bash
cp .env.cloud.example .env
chmod 600 .env
```

Generar valores independientes para las claves internas:

```bash
openssl rand -hex 32
openssl rand -base64 32
```

Editar `.env`:

```bash
nano .env
```

Completar:

- Clave interna de Hermes.
- Credenciales de OpenRouter, Replicate, Hugging Face y FAL que se utilicen.
- Hosts y orígenes autorizados.
- Límite de subida.
- Versiones de imágenes y modelos.

No copiar secretos en tickets, commits, capturas o logs.

Validar la configuración sin imprimir el contenido completo:

```bash
docker compose config --quiet
docker compose config --services
```

El segundo comando debe mostrar, como mínimo:

```text
media-web
media-api
media-core
hermes-agent
media-init
```

## 7. Construir e inicializar

```bash
docker compose build
```

Ejecutar la tarea de inicialización declarada por Compose. Esta descarga las voces de Piper, descarga el modelo de embeddings cuando sea necesario y construye el índice:

```bash
docker compose run --rm media-init
```

Los modelos deben quedar en el volumen persistente de `data/models`, no dentro del contenedor. La primera ejecución necesita acceso a Hugging Face; las siguientes reutilizan los archivos existentes.

Comprobar que están presentes:

```bash
docker compose run --rm media-core \
  python -c "from media_core.config import TTS_MODELS_DIR; print(*sorted(p.name for p in TTS_MODELS_DIR.glob('*.onnx')), sep='\\n')"
```

Ejecutar cualquier inicialización de Hermes documentada por el `compose.yaml`. Esta configuración debe persistir en su volumen y no repetirse en cada arranque.

## 8. Arrancar MedIA

```bash
docker compose up -d
docker compose ps
```

Todos los servicios deben aparecer como `running` y, cuando corresponda, `healthy`.

Revisar los logs iniciales:

```bash
docker compose logs --tail=100 media-core
docker compose logs --tail=100 hermes-agent
docker compose logs --tail=100 media-api
docker compose logs --tail=100 media-web
```

No continuar si aparecen errores de permisos, credenciales, modelos o conexión entre servicios.

## 9. Comprobar la aplicación desde la VM

Los nombres exactos de los endpoints deben coincidir con los health checks implementados durante la preparación:

```bash
curl --fail http://127.0.0.1:3000/
docker compose exec media-api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/api/health/ready').status)"
docker compose exec media-core \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').status)"
```

Si los endpoints finales tienen otra ruta, utilizar la definida en `compose.yaml`.

## 10. Publicar Media Web dentro de Tailscale

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:3000
tailscale serve status
```

Tailscale mostrará una URL HTTPS terminada en `.ts.net`. Abrirla desde los dispositivos autorizados.

No utilizar Tailscale Funnel: haría el servicio accesible desde Internet. Media API, Media Core y Hermes deben seguir sin puertos públicos.

## 11. Ejecutar la prueba funcional

Desde Media Web:

1. Iniciar sesión o confirmar el acceso autorizado.
2. Subir un vídeo pequeño.
3. Transcribirlo.
4. Ejecutar una búsqueda.
5. Ejecutar OCR o análisis visual.
6. Generar y descargar un clip.
7. Enviar una consulta a Hermes.

Reiniciar el stack:

```bash
docker compose restart
docker compose ps
```

Confirmar que el vídeo, la transcripción, el historial y los resultados siguen disponibles.

## 12. Configurar backups

Ejecutar el mecanismo de backup incluido durante la preparación del proyecto:

```bash
cd /opt/media
./scripts/backup-cloud.sh
```

El backup debe incluir datos de MedIA, índices, modelos necesarios, estado de Hermes y configuración cifrada. Copiarlo a una ubicación distinta de la VM.

Probar la restauración en otra VM:

```bash
./scripts/restore-cloud.sh RUTA_DEL_BACKUP
docker compose up -d
```

Mantener además snapshots automáticos del proveedor, pero no utilizarlos como única copia.

## 13. Operación habitual

```bash
# Estado
docker compose ps

# Logs
docker compose logs -f

# Reiniciar un servicio
docker compose restart media-api

# Parar conservando datos
docker compose down

# Arrancar
docker compose up -d
```

No ejecutar `docker compose down -v`: elimina los volúmenes persistentes.

Vigilar regularmente:

- Espacio en disco con `df -h`.
- Memoria con `free -h`.
- Reinicios de contenedores.
- Errores de proveedores y autenticación.
- Coste de APIs.
- Antigüedad del último backup restaurable.

## 14. Actualizar

1. Leer las notas de la nueva versión.
2. Ejecutar un backup y snapshot.
3. Descargar el nuevo tag Git.
4. Construir o descargar las nuevas imágenes.
5. Recrear los servicios.
6. Repetir la prueba funcional.

```bash
cd /opt/media
./scripts/backup-cloud.sh
git fetch --tags
git checkout NUEVA_VERSION
docker compose build
docker compose up -d
docker compose ps
```

Para volver atrás:

```bash
git checkout VERSION_ANTERIOR
docker compose build
docker compose up -d
```

Si cambió el formato de los datos, restaurar también el backup o snapshot compatible.

## 15. Cuándo usar un dominio público

No es necesario para el equipo si Tailscale cubre el acceso. Para publicar MedIA a usuarios externos se debe añadir antes:

- Dominio y proxy HTTPS.
- OIDC o SSO.
- Autorización en Media API.
- Rate limiting y protección contra abuso.
- Aislamiento y cuotas por usuario o equipo.

No abrir los puertos internos directamente en el firewall.

## Verificación final

- [ ] Docker y Compose instalados desde el repositorio oficial.
- [ ] Repositorio desplegado desde una versión concreta.
- [ ] `.env` con permisos `0600`.
- [ ] Compose validado.
- [ ] Todos los servicios saludables.
- [ ] Solo Media Web accesible mediante Tailscale.
- [ ] Prueba funcional completa.
- [ ] Datos persistentes tras reiniciar.
- [ ] Backup externo creado.
- [ ] Restauración probada.
- [ ] Procedimiento de actualización y rollback comprobado.

## Referencias

- [Preparar MedIA para cloud](despliegue-en-la-nube.md)
- [README del proyecto](../../README.md)
- [Docker Engine en Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose en Linux](https://docs.docker.com/compose/install/linux/)
- [Tailscale para Linux](https://tailscale.com/download/linux)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)

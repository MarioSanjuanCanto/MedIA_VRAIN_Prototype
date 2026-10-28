'use client';
import { useEffect, useRef, useState } from 'react';
import { AudioLines, Check, ChevronLeft, Download, Film, FolderOpen, Gauge, LoaderCircle, MessageSquare, Play, Search, Settings2, Trash2, Upload, X, AlertCircle, Sparkles } from 'lucide-react';
import HermesChat, { startFreshHermesChat } from './hermes-chat';

type Video = {
  id: string;
  filename: string;
  title: string;
  duration_seconds: number;
  language: string;
  size_mb: number;
  status: string;
  indexing_stage?: string;
  indexed: boolean;
  visual_indexed?: boolean;
  url: string;
  thumbnail: string;
};
type Health = { api: boolean; media_core: boolean; hermes_installed: boolean; hermes_running: boolean; hermes_mode?: string };
type ProviderCost = { id: string; name: string; cost_usd: number | null; requests: number | null; cost_type: 'actual' | 'estimated' | 'local' | 'unavailable'; configured: boolean; complete: boolean; detail: string };
type Costs = { currency: 'USD'; period: string; actual_cost_usd: number; estimated_cost_usd: number; total_cost_usd: number; complete: boolean; providers: ProviderCost[]; refreshing?: boolean; refreshed_at?: string };
const languages: Record<string, string> = { es: 'Español', en: 'English', ca: 'Català' };
function mediaUiText(value: string) { return value.replace(/\bHermes(?: WebUI| Desktop)?\b/g, 'MedIA'); }
function time(seconds: number) { return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`; }
function savedCosts(): Costs | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = JSON.parse(sessionStorage.getItem('media-api-costs') || 'null') as Costs | null;
    return value?.period === new Date().toISOString().slice(0, 7) ? value : null;
  } catch { return null; }
}
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, init);
  if (!res.ok) { const data = await res.json().catch(() => null); throw new Error(typeof data?.detail === 'string' ? data.detail : `No se pudo completar la petición (${res.status}). Comprueba la API.`); }
  return res.json();
}

export default function Home() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [health, setHealth] = useState<Health | null>(null), [loading, setLoading] = useState(true);
  const [costs, setCosts] = useState<Costs | null>(null), [costsLoading, setCostsLoading] = useState(false), [costsError, setCostsError] = useState(false), [reportLoading, setReportLoading] = useState(false);
  const [tab, setTab] = useState('chat'), [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Video | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Video | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [error, setError] = useState(''), [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0), [settings, setSettings] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadedMeta, setUploadedMeta] = useState<{ name: string; sizeMb: number } | null>(null);
  const [autoIndexingVideo, setAutoIndexingVideo] = useState<string | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const anyIndexing = videos.some(v => v.status === 'indexing');
  async function refresh() {
    const v = await api<Video[]>('/videos'); setVideos(v);
    return v;
  }
  async function loadLibrary() {
    setLoading(true); setLoadError(false); setError('');
    try { await refresh(); } catch (e) { setLoadError(true); setError((e as Error).message); } finally { setLoading(false); }
  }
  useEffect(() => {
    loadLibrary();
    const check = () => api<Health>('/health').then(setHealth).catch(() => setHealth(null));
    check(); const timer = setInterval(check, 20000); return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (tab !== 'costs') return;
    let stopped = false, timer: number | undefined;
    const cached = costs || savedCosts();
    let hasVisibleCosts = Boolean(cached);
    if (cached && !costs) setCosts(cached);
    const loadCosts = async () => {
      if (!hasVisibleCosts) setCostsLoading(true);
      setCostsError(false);
      try {
        const value = await api<Costs>('/costs');
        if (stopped) return;
        hasVisibleCosts = true;
        setCosts(value); sessionStorage.setItem('media-api-costs', JSON.stringify(value));
        if (value.refreshing) timer = window.setTimeout(loadCosts, 2500);
      } catch { if (!stopped) setCostsError(true); }
      finally { if (!stopped) setCostsLoading(false); }
    };
    loadCosts();
    return () => { stopped = true; if (timer) window.clearTimeout(timer); };
  }, [tab]);
  useEffect(() => {
    if (!anyIndexing && !autoIndexingVideo) return;
    let stopped = false;
    const poll = async () => {
      try {
        const updated = await api<Video[]>('/videos');
        if (stopped) return;
        setVideos(updated);
        if (autoIndexingVideo) {
          const current = updated.find(v => v.filename === autoIndexingVideo);
          if (current && current.status !== 'indexing') {
            setAutoIndexingVideo(null);
          }
        }
      } catch {}
    };
    const timer = setInterval(poll, 2200);
    return () => { stopped = true; clearInterval(timer); };
  }, [anyIndexing, autoIndexingVideo]);
  useEffect(() => {
    setSelected(current => current ? videos.find(v => v.filename === current.filename) || null : null);
  }, [videos]);
  useEffect(() => {
    if (!settings && !deleteTarget) return;
    const previous = document.activeElement as HTMLElement | null;
    const modal = document.querySelector<HTMLElement>('[role="dialog"]');
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !deleting) { setSettings(false); setDeleteTarget(null); }
      if (event.key !== 'Tab' || !modal) return;
      const controls = Array.from(modal.querySelectorAll<HTMLElement>('button, a[href], input, [tabindex="0"]'));
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keydown);
    return () => { document.removeEventListener('keydown', keydown); previous?.focus(); };
  }, [settings, deleteTarget, deleting]);
  function choose(v: Video) { setSelected(v); setTab('library'); setError(''); }
  async function deleteVideo(video: Video) {
    setDeleting(true); setError('');
    try {
      await api(`/videos/${encodeURIComponent(video.filename)}`, { method: 'DELETE' });
      setVideos(current => current.filter(item => item.filename !== video.filename));
      setSelected(current => current?.filename === video.filename ? null : current);
      setDeleteTarget(null);
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setDeleting(false); }
  }
  async function startIndexing(filename: string) {
    try {
      setError('');
      setAutoIndexingVideo(filename);
      await api(`/videos/${encodeURIComponent(filename)}/index`, { method: 'POST' });
      await refresh();
    } catch (e) {
      setAutoIndexingVideo(null);
      setError((e as Error).message);
    }
  }
  async function uploadFile(file?: File) {
    if (!file) return;
    setUploading(true); setUploadProgress(0); setError('');
    const sizeMb = +(file.size / (1024 * 1024)).toFixed(1);
    setUploadedMeta({ name: file.name, sizeMb });
    try {
      const uploadUrl = process.env.NEXT_PUBLIC_MEDIA_UPLOAD_URL || '/api/videos';
      const data = await new Promise<{filename: string; status?: string; stage?: string}>((resolve, reject) => {
        const xhr = new XMLHttpRequest(); xhr.open('POST', uploadUrl);
        xhr.upload.onprogress = e => { if (e.lengthComputable) setUploadProgress(Math.round(e.loaded / e.total * 100)); };
        xhr.onerror = () => reject(new Error('La conexión se interrumpió durante la subida.'));
        xhr.onload = () => { try { const result = JSON.parse(xhr.responseText); xhr.status < 300 ? resolve(result) : reject(new Error(result.detail || 'La subida ha fallado.')); } catch { reject(new Error('No se pudo subir el vídeo. Comprueba el tamaño y la API.')); } };
        const form = new FormData(); form.append('file', file); xhr.send(form);
      });
      setAutoIndexingVideo(data.filename);
      await refresh();
    } catch (e) { setError((e as Error).message); } finally { setUploading(false); if (upload.current) upload.current.value = ''; }
  }
  function handleDragOver(e: React.DragEvent) { e.preventDefault(); e.stopPropagation(); setIsDragging(true); }
  function handleDragLeave(e: React.DragEvent) { e.preventDefault(); e.stopPropagation(); setIsDragging(false); }
  function handleDrop(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation(); setIsDragging(false);
    if (uploading) return;
    const file = e.dataTransfer.files?.[0];
    if (file) uploadFile(file);
  }
  async function downloadCostReport() {
    if (!costs || reportLoading) return;
    setReportLoading(true); setError('');
    try {
      const response = await fetch('/api/costs/report.pdf', { cache: 'no-store' });
      const contentType = response.headers.get('content-type') || '';
      if (!response.ok || !contentType.includes('application/pdf')) {
        const data = contentType.includes('application/json') ? await response.json().catch(() => null) : null;
        const detail = typeof data?.detail === 'string' && data.detail !== 'Not Found' ? data.detail : '';
        throw new Error(detail || 'El informe PDF aún no está disponible. Reinicia la API y vuelve a intentarlo.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = `uso-apis-${costs.period}.pdf`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError((e as Error).message); }
    finally { setReportLoading(false); }
  }
  const visible = videos.filter(v => `${v.id} ${v.title}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="shell">
    <aside className="sidebar">
      <button className="brand" onClick={() => {setSelected(null); setTab('library');}} aria-label="MedIA, ir a biblioteca"><span className="brand-mark"><AudioLines size={26}/></span>MedIA<span className="brand-dot">.</span></button>
      <p className="workspace-name">Estudio audiovisual</p>
      <nav aria-label="Principal">
        <button className={tab === 'chat' ? 'nav active' : 'nav'} onClick={() => {setTab('chat'); setSelected(null); setError('');}}><MessageSquare size={19}/>Chat con MedIA</button>
        <button className={tab === 'library' ? 'nav active' : 'nav'} onClick={() => {setTab('library'); setSelected(null);}}><FolderOpen size={19}/>Biblioteca<span className="count">{videos.length}</span></button>
        <button className={tab === 'costs' ? 'nav active' : 'nav'} onClick={() => {setTab('costs'); setSelected(null);}}><Gauge size={19}/>Uso de APIs</button>
      </nav>
      <div className="sidebar-bottom"><div className="connection"><span className={health?.media_core ? 'dot online' : 'dot'}/><div><strong>{health?.media_core ? 'Procesamiento disponible' : 'Procesamiento no disponible'}</strong><span>Espacio de trabajo local</span></div></div><button className="nav" onClick={() => setSettings(true)}><Settings2 size={18}/>Conexiones</button></div>
    </aside>
    <main>
      <header className="topbar"><span>MedIA <span className="slash">/</span> {selected ? selected.id : tab === 'chat' ? 'Chat con MedIA' : tab === 'library' ? 'Biblioteca' : 'Uso de APIs'}</span><span className="local-badge">En este equipo</span></header>
      {error && <div className="error-banner" role="alert"><AlertCircle size={19}/><span>{mediaUiText(error)}</span><button aria-label="Cerrar aviso" onClick={() => setError('')}><X size={17}/></button></div>}
      {!selected && tab === 'chat' ? <HermesChat available={Boolean(health?.hermes_installed)} mode={health?.hermes_mode} /> : !selected ? <div className="library">
        {tab === 'costs' ? <section className="cost-dashboard" aria-labelledby="cost-title">
          <div className="page-heading"><div><h1 id="cost-title">Uso de APIs</h1><p>Consumo registrado durante el mes actual.</p></div><div className="cost-heading-actions"><span className="cost-period">{costs ? new Date(`${costs.period}-02`).toLocaleDateString('es-ES', {month: 'long', year: 'numeric'}) : 'Mes actual'}{costs?.refreshing && <small>Actualizando…</small>}</span>{costs && <button className="cost-download" type="button" disabled={reportLoading} onClick={downloadCostReport}>{reportLoading ? <LoaderCircle className="spin" size={15}/> : <Download size={15}/>} {reportLoading ? 'Preparando…' : 'Descargar PDF'}</button>}</div></div>
          {costsLoading ? <div className="empty"><LoaderCircle className="spin"/>Cargando consumo…</div> : costsError ? <div className="empty"><AlertCircle size={30}/><h2>No se pudo cargar el uso</h2><p>Comprueba que la API esté iniciada.</p></div> : costs ? <>
            <div className="cost-total"><span>Total conocido</span><strong>{costs.total_cost_usd.toLocaleString('es-ES', {style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4})}</strong><small>{costs.estimated_cost_usd ? `${costs.actual_cost_usd.toLocaleString('es-ES', {style: 'currency', currency: 'USD'})} real · ${costs.estimated_cost_usd.toLocaleString('es-ES', {style: 'currency', currency: 'USD'})} estimado` : 'Importe real informado por los proveedores disponibles'}</small></div>
            <div className="provider-spend" aria-label="Uso por proveedor">{costs.providers.map(provider => <article className="provider-row" key={provider.id}>
              <div className={`provider-mark ${provider.id}`} aria-hidden="true">{provider.name.slice(0, 1)}</div>
              <div className="provider-name"><h2>{provider.name}</h2><p>{provider.requests !== null ? `${provider.requests} ${provider.requests === 1 ? 'llamada' : 'llamadas'} · ` : ''}{provider.detail}</p></div>
              <div className="provider-amount"><strong>{provider.cost_usd === null ? '—' : provider.cost_usd.toLocaleString('es-ES', {style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4})}</strong><span>{provider.cost_type === 'actual' ? 'Real' : provider.cost_type === 'estimated' ? 'Estimado' : provider.cost_type === 'local' ? 'Local' : 'No disponible'}</span></div>
            </article>)}</div>
            <p className="cost-note">Consulta realizada desde el backend. Las claves nunca se envían al navegador; Replicate se marca como estimación cuando no publica el cargo final.</p>
          </> : null}
        </section> : <>
        <div className="page-heading">
          <div>
            <div className="library-title-row">
              <h1>Biblioteca de vídeos</h1>
              <span className="library-folder-pill" title="Carpeta en disco local">
                <FolderOpen size={14} />
                <code>test_vectors/videos</code>
              </span>
            </div>
            <p>
              Vídeos disponibles en la carpeta <code>test_vectors/videos</code>. Al subir un vídeo se guarda directamente en esta carpeta y se indexa automáticamente (audio WhisperX + vectores CLIP de fotogramas).
            </p>
          </div>
          <button className="primary upload-cta-btn" disabled={uploading} onClick={() => upload.current?.click()}>
            {uploading ? <LoaderCircle className="spin" size={17} /> : <Upload size={17} />}
            {uploading ? (uploadProgress === 100 ? 'Guardando en test_vectors/videos…' : `Subiendo ${uploadProgress}%`) : 'Añadir vídeo'}
          </button>
        </div>

        {/* Dropzone interactiva con soporte Drag & Drop */}
        <div
          className={`upload-dropzone ${isDragging ? 'dragging' : ''} ${uploading ? 'uploading' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => !uploading && upload.current?.click()}
          role="region"
          aria-label="Zona de subida de vídeos"
        >
          {uploading ? (
            <div className="dropzone-uploading" onClick={e => e.stopPropagation()}>
              <div className="uploading-header">
                <div className="uploading-icon-box">
                  <Upload size={22} className="spin" />
                </div>
                <div className="uploading-info">
                  <strong>Guardando en test_vectors/videos…</strong>
                  <span>{uploadedMeta?.name} · {uploadedMeta?.sizeMb} MB</span>
                </div>
                <span className="uploading-percentage">{uploadProgress}%</span>
              </div>
              <div className="uploading-progress-track">
                <div className="uploading-progress-bar" style={{ width: `${uploadProgress}%` }} />
              </div>
              <p className="uploading-status-note">
                {uploadProgress === 100
                  ? 'Verificando archivo e iniciando indexación automática...'
                  : 'Subiendo archivo directamente a la carpeta de test_vectors...'}
              </p>
            </div>
          ) : (
            <div className="dropzone-idle">
              <div className="dropzone-left">
                <div className="dropzone-icon-box">
                  <Upload size={24} />
                </div>
                <div className="dropzone-text">
                  <strong>Arrastra tu vídeo aquí o haz clic para explorar</strong>
                  <p>Se guardará en <code>test_vectors/videos</code> y se indexará automáticamente</p>
                </div>
              </div>
              <div className="dropzone-formats">
                <span>MP4</span>
                <span>MOV</span>
                <span>MKV</span>
                <span>WebM</span>
                <small>Hasta 4 GB</small>
              </div>
            </div>
          )}
        </div>

        {/* Banner de indexación activa */}
        {autoIndexingVideo && (
          <div className="auto-index-banner" role="status">
            <LoaderCircle className="spin" size={20} />
            <div className="auto-index-banner-body">
              <strong>Indexando automáticamente: <code>{autoIndexingVideo}</code></strong>
              <span>
                {videos.find(v => v.filename === autoIndexingVideo)?.indexing_stage || 'Transcribiendo audio y extrayendo fotogramas clave...'}
              </span>
            </div>
            <span className="auto-index-tag">Indexación automática activa</span>
          </div>
        )}

        <div className="library-tools">
          <div className="section-title">
            <Film size={19} />
            <h2>Vídeos en la carpeta</h2>
            <span>{videos.length}</span>
          </div>
          <label className="search">
            <Search size={18} />
            <input aria-label="Buscar vídeos" placeholder="Buscar en test_vectors/videos…" value={query} onChange={e => setQuery(e.target.value)} />
          </label>
        </div>

        {loading ? (
          <div className="empty"><LoaderCircle className="spin" />Cargando carpeta de vídeos…</div>
        ) : loadError ? (
          <div className="empty">
            <AlertCircle size={30} />
            <h2>No se pudo leer la carpeta de vídeos</h2>
            <p>Comprueba que la API esté iniciada y vuelve a intentarlo.</p>
            <button className="secondary" onClick={loadLibrary}>Volver a cargar</button>
          </div>
        ) : visible.length ? (
          <div className="video-grid">
            {visible.map(v => {
              const isIndexing = v.status === 'indexing' || autoIndexingVideo === v.filename;
              return (
                <div key={v.filename} className={`video-card-container ${isIndexing ? 'is-indexing' : ''}`}>
                  <button className="video-card" onClick={() => choose(v)}>
                    <div className="thumbnail">
                      <img
                        src={v.thumbnail}
                        alt={`Fotograma de ${v.id}`}
                        loading="lazy"
                        onError={e => { e.currentTarget.style.visibility = 'hidden'; }}
                      />
                      <Film className="thumbnail-fallback" />
                      <span className="play-overlay"><Play size={20} fill="currentColor" /></span>
                      {!!v.duration_seconds && <span className="duration">{time(v.duration_seconds)}</span>}
                      {isIndexing && (
                        <span className="card-indexing-badge">
                          <LoaderCircle className="spin" size={12} /> Indexando…
                        </span>
                      )}
                    </div>
                    <div className="video-info">
                      <div className="video-title">
                        <h3 title={v.title}>{v.title}</h3>
                        <span className={`status-pill ${v.status === 'ready' ? 'ready' : isIndexing ? 'indexing' : 'unverified'}`}>
                          {v.status === 'ready' ? (
                            <><Check size={11} /> Listo</>
                          ) : isIndexing ? (
                            <><LoaderCircle className="spin" size={11} /> Indexando…</>
                          ) : (
                            <><AlertCircle size={11} /> Sin indexar</>
                          )}
                        </span>
                      </div>
                      <div className="video-meta-line">
                        <span className="video-filename" title={v.filename}>{v.filename}</span>
                        <span className="video-filesize">{v.size_mb} MB</span>
                      </div>
                      {isIndexing && v.indexing_stage && (
                        <p className="indexing-stage-text">{v.indexing_stage}</p>
                      )}
                    </div>
                  </button>
                  <div className="video-card-bottom-bar">
                    <button
                      className="delete-video-button"
                      title={isIndexing ? 'Espera a que termine la indexación' : `Eliminar ${v.title}`}
                      disabled={isIndexing || deleting}
                      onClick={() => { setError(''); setDeleteTarget(v); }}
                    >
                      <Trash2 size={13} /> Quitar vídeo
                    </button>
                    {v.status !== 'ready' && !isIndexing && (
                      <button
                        className="quick-index-button"
                        title="Indexar este vídeo automáticamente"
                        onClick={() => startIndexing(v.filename)}
                      >
                        <Sparkles size={13} /> Indexar vídeo
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty">
            <FolderOpen size={34} />
            <h2>{query ? 'No encontramos ese vídeo' : 'La carpeta test_vectors/videos está vacía'}</h2>
            <p>{query ? 'Prueba con otro término de búsqueda.' : 'Arrastra un vídeo a la zona de subida superior o haz clic para añadir tu primer vídeo.'}</p>
          </div>
        )}
        <div className="library-note">
          <Film size={19} />
          <p>
            Almacenamiento directo en <code>data/test-vectors/videos</code>.
            <span>Los vídeos se indexan semánticamente con WhisperX y visualmente con CLIP para búsquedas instantáneas y extracción de clips.</span>
          </p>
        </div>
        </>}
      </div> : <div className="studio video-viewer">
        <button className="back" onClick={() => { setSelected(null); setTab('library'); }}><ChevronLeft size={17}/>Volver a la biblioteca</button>
        <div className="studio-heading">
          <div>
            <h1>{selected.title}</h1>
            <p>{languages[selected.language] || 'Idioma por detectar'} {selected.duration_seconds ? `· ${time(selected.duration_seconds)}` : ''} · {selected.size_mb} MB</p>
          </div>
        </div>
        <div className="video-viewer-content">
          <div className="player-wrap">
            <video key={selected.url} src={selected.url} poster={selected.thumbnail} controls preload="metadata" onError={() => setError('El navegador no puede reproducir este vídeo. Comprueba la conexión y el códec del archivo.')}/>
            <div className="player-label"><span>Vídeo original</span></div>
          </div>
          <div className="video-chat-action">
            <button className="primary" onClick={() => { startFreshHermesChat(); setSelected(null); setTab('chat'); setError(''); }}><MessageSquare size={17}/>Iniciar Chat</button>
          </div>
        </div>
      </div>}
    </main>
    <input ref={upload} type="file" accept=".mp4,.mov,.mkv,.webm,.m4v" hidden onChange={e => uploadFile(e.target.files?.[0])}/>
    {deleteTarget && <div className="modal-backdrop" onClick={() => !deleting && setDeleteTarget(null)}>
      <section className="modal delete-video-modal" role="dialog" aria-modal="true" aria-labelledby="delete-video-title" onClick={e => e.stopPropagation()}>
        <h2 id="delete-video-title">¿Quitar este vídeo?</h2>
        <p><strong>{deleteTarget.title}</strong> se eliminará del disco junto con su transcripción, fotogramas e índices. Esta acción no se puede deshacer.</p>
        {error && <p className="delete-video-error" role="alert">{mediaUiText(error)}</p>}
        <div className="delete-video-actions">
          <button className="secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>Cancelar</button>
          <button className="danger-button" disabled={deleting} onClick={() => deleteVideo(deleteTarget)}>{deleting ? <LoaderCircle className="spin" size={16}/> : <Trash2 size={16}/>} {deleting ? 'Eliminando…' : 'Quitar vídeo'}</button>
        </div>
      </section>
    </div>}
    {settings && <div className="modal-backdrop" onClick={() => setSettings(false)}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={e => e.stopPropagation()}><button className="modal-close" autoFocus aria-label="Cerrar conexiones" onClick={() => setSettings(false)}><X/></button><h2 id="settings-title">Conexiones locales</h2><p>El procesamiento utiliza la configuración de tu equipo.</p><div className="connection-row"><strong>API de MedIA</strong><span>{health?.api ? 'Conectada' : 'No disponible'}</span></div><div className="connection-row"><strong>media-core</strong><span>{health?.media_core ? 'Conectado' : 'Arranca el servidor en 8001'}</span></div><div className="connection-row"><strong>MedIA</strong><span>{health?.hermes_running ? 'Proceso conectado' : health?.hermes_installed ? 'Instalado · se inicia al enviar' : 'Configura su ruta en la API'}</span></div><p className="settings-note">La voz usa Piper en media-core. La transcripción y los resúmenes conservan los proveedores configurados en el MCP.</p></section></div>}

  </div>;
}

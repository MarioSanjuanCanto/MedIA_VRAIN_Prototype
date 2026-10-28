import { Fragment, useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowUp, AudioLines, Bot, Check, Download, Film, Image as ImageIcon, LoaderCircle, MessageSquare, Plus, Square } from 'lucide-react';
import commandDefinitions from './chat-commands.json';
import { canStartNewConversation, isCurrentConversation } from './chat-lifecycle';
import VoiceAvatar from './voice-avatar';

type Artifact = {
  kind: string;
  name: string;
  url: string;
};

type ChatActivity = {
  kind?: 'skill' | 'tool';
  name?: string;
  label?: string;
  status?: 'running' | 'completed';
  at?: number;
};

type ChatJob = {
  id: string;
  kind: 'hermes_chat';
  conversation_id: string;
  message: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  stage: string;
  created_at: number;
  elapsed_seconds?: number;
  answer: string;
  error?: string;
  progress?: number;
  artifacts?: Artifact[];
  events?: ChatActivity[];
};

type HermesChatProps = {
  available: boolean;
  mode?: string;
};

type SystemHealth = {
  api: boolean;
  media_core: boolean;
  hermes_installed: boolean;
  hermes_running: boolean;
  hermes_mode?: string;
};

type VideoSummary = {
  id: string;
  title: string;
  filename: string;
  status: string;
  indexed: boolean;
};

type ReasoningEffort = 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

type ReasoningStatus = {
  reasoning_effort?: ReasoningEffort;
  effort?: ReasoningEffort;
};

type DeleteChatResult = {
  conversation_id: string;
  deleted_jobs: number;
};

const storageKey = 'media-hermes-conversation';
export function startFreshHermesChat() {
  window.localStorage.removeItem(storageKey);
}
const reasoningStorageKey = 'media-hermes-reasoning';
const defaultReasoningEffort: ReasoningEffort = 'medium';
const reasoningOptions: { value: ReasoningEffort; description: string }[] = [
  { value: 'minimal', description: 'Respuesta rápida con razonamiento mínimo.' },
  { value: 'low', description: 'Razonamiento ligero para tareas sencillas.' },
  { value: 'medium', description: 'Equilibrio entre velocidad y profundidad.' },
  { value: 'high', description: 'Más análisis para tareas complejas.' },
  { value: 'xhigh', description: 'Análisis muy profundo.' },
  { value: 'max', description: 'Máxima fuerza de razonamiento disponible.' },
];
const reasoningEfforts = reasoningOptions.map(option => option.value);

function mediaUiText(value: string) {
  return value.replace(/\bHermes(?: WebUI| Desktop)?\b/g, 'MedIA');
}

function activityText(activity: ChatActivity) {
  if (activity.kind === 'skill') return `Skill: ${activity.label || activity.name || 'desconocida'}`;
  const name = (activity.name || 'procesando').replace(/^mcp__media_core__/, '');
  return `Herramienta: ${name}`;
}

function statusReasoning(status: ReasoningStatus, fallback: ReasoningEffort) {
  const value = status.reasoning_effort || status.effort;
  return reasoningEfforts.includes(value as ReasoningEffort) ? value as ReasoningEffort : fallback;
}
const suggestions = [
  '¿Qué vídeos hay disponibles?',
  '¿Qué vídeos tienen transcripción?',
  '¿Qué puedo consultar sobre mis vídeos?',
];
const slashCommands = Object.entries(commandDefinitions).map(([name, definition]) => ({ name, ...definition }));
const commandHelpText = [
  'Lista de estructuras de comando:',
  '',
  ...slashCommands
    .filter(command => command.name !== '/ayuda')
    .map(command => `- ${command.usage} — ${command.description}`),
].join('\n');

function expandSlashCommand(message: string) {
  const clean = message.trim();
  const match = /^(\/\S+)(?:\s+([\s\S]*))?$/.exec(clean);
  if (!match) return clean;
  const definition = commandDefinitions[match[1].toLowerCase() as keyof typeof commandDefinitions];
  if (!definition) return clean;
  const input = match[2]?.trim() || definition.emptyInput;
  return definition.prompt.replace('{{input}}', input);
}

function healthDiagnostic(health: SystemHealth | null) {
  if (!health) {
    return [
      'Mini diagnóstico de servicios:',
      '',
      '- Media API: no responde.',
      '- Media Core (MCP): no se puede verificar sin Media API.',
      '- MedIA: no se puede verificar sin Media API.',
      '',
      'Diagnóstico: revisa que Media API esté arrancada y accesible.',
    ].join('\n');
  }

  const issues: string[] = [];
  if (!health.media_core) issues.push('Media Core no responde o el endpoint no corresponde al MCP esperado');
  if (!health.hermes_installed) issues.push('MedIA no está instalada o configurada');
  else if (!health.hermes_running) issues.push('MedIA está instalada, pero no está ejecutándose');
  const hermesState = health.hermes_running
    ? `operativo${health.hermes_mode ? ` · modo ${health.hermes_mode}` : ''}`
    : health.hermes_installed ? 'instalado, pero detenido' : 'no disponible';

  return [
    'Mini diagnóstico de servicios:',
    '',
    `- Media API: ${health.api ? 'operativa' : 'no disponible'}.`,
    `- Media Core (MCP): ${health.media_core ? 'conectado y con herramientas disponibles' : 'sin conexión o respuesta válida'}.`,
    `- MedIA: ${hermesState}.`,
    '',
    issues.length
      ? `Diagnóstico: necesita atención — ${issues.join('; ')}.`
      : 'Diagnóstico: todos los servicios esenciales responden correctamente.',
  ].join('\n');
}

function videoListText(videos: VideoSummary[] | null) {
  if (!videos) return 'Vídeos disponibles:\n\nNo se pudo consultar Media API.';
  if (!videos.length) return 'Vídeos disponibles:\n\nNo hay vídeos cargados.';
  const statusLabel = (video: VideoSummary) => {
    if (video.status === 'ready') return video.indexed ? 'listo e indexado' : 'listo';
    if (video.status === 'unverified') return 'necesita verificación';
    return 'pendiente de procesar';
  };
  return [
    'Vídeos disponibles:',
    '',
    ...videos.map(video => `- ${video.id} — ${video.title || video.filename} · ${statusLabel(video)}`),
    '',
    'Estados:',
    '- Listo: tiene una transcripción verificada y puede utilizarse.',
    '- Listo e indexado: además puede buscarse por palabras o significado con /buscar.',
  ].join('\n');
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, init);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : `No se pudo completar la petición (${response.status}). Comprueba la API.`,
    );
  }
  return response.json();
}

function getJobMedia(job: ChatJob): Artifact[] {
  const list: Artifact[] = [];
  const seenUrls = new Set<string>();

  if (Array.isArray(job.artifacts)) {
    for (const a of job.artifacts) {
      if (a?.url && !seenUrls.has(a.url)) {
        seenUrls.add(a.url);
        list.push(a);
      }
    }
  }

  if (job.answer) {
    // 1. Direct API file paths: /api/files/(clip|audio|video)/name
    const apiRegex = /\/api\/files\/(clip|audio|video)\/([^\s"')]+)/g;
    let match: RegExpExecArray | null;
    while ((match = apiRegex.exec(job.answer)) !== null) {
      const url = match[0];
      if (!seenUrls.has(url)) {
        seenUrls.add(url);
        list.push({ kind: match[1], name: decodeURIComponent(match[2]), url });
      }
    }

    // 2. Output clips paths (e.g. .../output_clips/TV05_clip_...mp4)
    const clipRegex = /(?:output_clips[\\/])([a-zA-Z0-9_\-.]+\.(?:mp4|webm))/gi;
    while ((match = clipRegex.exec(job.answer)) !== null) {
      const name = match[1];
      const url = `/api/files/clip/${encodeURIComponent(name)}`;
      if (!seenUrls.has(url)) {
        seenUrls.add(url);
        list.push({ kind: 'clip', name, url });
      }
    }

    // 3. Output audio paths (e.g. .../output_audio/resumen_...wav)
    const audioRegex = /(?:output_audio[\\/])([a-zA-Z0-9_\-.]+\.(?:wav|mp3|ogg|m4a))/gi;
    while ((match = audioRegex.exec(job.answer)) !== null) {
      const name = match[1];
      const url = `/api/files/audio/${encodeURIComponent(name)}`;
      if (!seenUrls.has(url)) {
        seenUrls.add(url);
        list.push({ kind: 'audio', name, url });
      }
    }

    // 4. Keyframe images (supports MEDIA:...keyframes/TV04/frame_0514.jpg, /api/keyframes/..., etc.)
    const keyframeRegex = /(?:keyframes[\\/]|(?:\/api\/keyframes\/))([a-zA-Z0-9_-]+)[\\/]([a-zA-Z0-9_\-.]+\.(?:jpg|jpeg|png))/gi;
    while ((match = keyframeRegex.exec(job.answer)) !== null) {
      const vid = match[1].toUpperCase();
      const fname = match[2];
      const url = `/api/keyframes/${vid}/${encodeURIComponent(fname)}`;
      if (!seenUrls.has(url)) {
        seenUrls.add(url);
        list.push({ kind: 'image', name: `Fotograma ${vid} · ${fname}`, url });
      }
    }
  }

  return list;
}

function renderFormattedText(text: string) {
  if (!text) return null;
  const paragraphs = text.split(/\n\n+/);
  return (
    <div className="chat-answer-body">
      {paragraphs.map((para, pIdx) => {
        const lines = para.split('\n');
        const header = parseTableRow(lines[0]);
        if (header && lines.length > 1 && isTableDivider(lines[1])) {
          const tableEnd = lines.findIndex((line, index) => index > 1 && !parseTableRow(line));
          const rowsEnd = tableEnd === -1 ? lines.length : tableEnd;
          const rows = lines.slice(2, rowsEnd).map(parseTableRow).filter((row): row is string[] => Boolean(row));
          return (
            <Fragment key={pIdx}>
              <div className="chat-table-wrap">
                <table>
                  <thead><tr>{header.map((cell, index) => <th key={index}>{renderLine(cell)}</th>)}</tr></thead>
                  <tbody>{rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>{header.map((_, cellIndex) => <td key={cellIndex}>{renderLine(row[cellIndex] || '')}</td>)}</tr>
                  ))}</tbody>
                </table>
              </div>
              {lines.slice(rowsEnd).map((line, index) => <p key={index}>{renderLine(line)}</p>)}
            </Fragment>
          );
        }
        const renderedLines = lines
          .map((line, lIdx) => {
            const content = renderLine(line);
            if (content === null) return null;
            return (
              <span key={lIdx}>
                {lIdx > 0 && <br />}
                {content}
              </span>
            );
          })
          .filter(Boolean);
        if (renderedLines.length === 0) return null;
        return <p key={pIdx}>{renderedLines}</p>;
      })}
    </div>
  );
}

function parseTableRow(line: string) {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return null;
  return trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
}

function isTableDivider(line: string) {
  const cells = parseTableRow(line);
  return Boolean(cells?.length && cells.every(cell => /^:?-{3,}:?$/.test(cell)));
}

function renderLine(line: string) {
  const trimmed = line.trim();
  if (trimmed.startsWith('MEDIA:')) {
    const rawPath = trimmed.slice(6).trim();
    const kfMatch = /(?:keyframes[\\/]|(?:\/api\/keyframes\/))([a-zA-Z0-9_-]+)[\\/]([a-zA-Z0-9_\-.]+\.(?:jpg|jpeg|png))/i.exec(rawPath);
    if (kfMatch) {
      const vid = kfMatch[1].toUpperCase();
      const fname = kfMatch[2];
      const url = `/api/keyframes/${vid}/${encodeURIComponent(fname)}`;
      return <img src={url} alt={`Fotograma ${vid} ${fname}`} className="chat-inline-image" />;
    }
    if (rawPath.match(/\.(?:mp4|webm|wav|mp3|ogg|m4a)$/i)) {
      return null;
    }
  }

  const parts: (string | React.ReactNode)[] = [];
  const regex = /(!?\[[^\]]*\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      parts.push(line.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('![') && token.endsWith(')')) {
      const imgMatch = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(token);
      if (imgMatch) {
        parts.push(<img key={key++} src={imgMatch[2]} alt={imgMatch[1]} className="chat-inline-image" />);
      }
    } else if (token.startsWith('[') && token.endsWith(')')) {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (linkMatch) {
        parts.push(<a key={key++} href={linkMatch[2]} target="_blank" rel="noopener noreferrer">{linkMatch[1]}</a>);
      }
    } else if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(<code key={key++}>{token.slice(1, -1)}</code>);
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex));
  }
  return parts.length ? parts : line;
}

export default function HermesChat({ available, mode }: HermesChatProps) {
  const [jobs, setJobs] = useState<ChatJob[]>([]);
  const [conversationId, setConversationId] = useState('');
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceLanguage, setVoiceLanguage] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [localCommandLabel, setLocalCommandLabel] = useState('');
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(defaultReasoningEffort);
  const [error, setError] = useState('');
  const [activeCommand, setActiveCommand] = useState(0);
  const [commandMenuDismissed, setCommandMenuDismissed] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const conversationEpoch = useRef(0);
  const pending = jobs.find(job => job.status === 'queued' || job.status === 'running');
  const busy = submitting || stopping || deleting || Boolean(localCommandLabel) || Boolean(pending);
  const localCommandRequested = ['/ayuda', '/estado', '/videos', '/new', '/reasoning']
    .includes(draft.trim().toLowerCase().split(/\s+/, 1)[0]);
  const commandQuery = draft.match(/^\/\S*$/)?.[0].toLowerCase();
  const filteredCommands = commandQuery
    ? slashCommands.filter(command => command.name.startsWith(commandQuery))
    : [];
  const reasoningMatch = /^\/reasoning(?:\s+(\S*))?$/.exec(draft.trimStart().toLowerCase());
  const reasoningQuery = reasoningMatch?.[1] || '';
  const filteredReasoningOptions = reasoningMatch
    ? reasoningOptions.filter(option => option.value.startsWith(reasoningQuery))
    : [];
  const reasoningMenuOpen = !commandMenuDismissed && filteredReasoningOptions.length > 0;
  const commandMenuOpen = !reasoningMenuOpen && !commandMenuDismissed && filteredCommands.length > 0;
  const autocompleteOpen = reasoningMenuOpen || commandMenuOpen;

  function selectCommand(command: (typeof slashCommands)[number]) {
    setDraft(command.name + ' ');
    if (command.name === '/reasoning') {
      setActiveCommand(reasoningEfforts.indexOf(reasoningEffort));
      setCommandMenuDismissed(false);
    } else {
      setCommandMenuDismissed(true);
    }
    textarea.current?.focus();
  }

  function selectReasoningOption(option: ReasoningEffort) {
    setDraft(`/reasoning ${option}`);
    setCommandMenuDismissed(true);
    textarea.current?.focus();
  }

  function appendLocalResult(command: string, answer: string, startedAt = Date.now()) {
    setJobs(previous => [...previous, {
      id: `local-${command.slice(1)}-${Date.now()}`,
      kind: 'hermes_chat',
      conversation_id: conversationId || 'local',
      message: command,
      status: 'completed',
      stage: '',
      created_at: startedAt / 1000,
      elapsed_seconds: Math.round((Date.now() - startedAt) / 100) / 10,
      answer,
      artifacts: [],
    }]);
    setDraft('');
    setCommandMenuDismissed(true);
    textarea.current?.focus();
  }

  useEffect(() => {
    const savedReasoning = window.localStorage.getItem(reasoningStorageKey);
    if (reasoningEfforts.includes(savedReasoning as ReasoningEffort)) {
      setReasoningEffort(savedReasoning as ReasoningEffort);
    }
    request<ReasoningStatus>('/reasoning', { cache: 'no-store' })
      .then(status => {
        const current = statusReasoning(status, defaultReasoningEffort);
        setReasoningEffort(current);
        window.localStorage.setItem(reasoningStorageKey, current);
      })
      .catch(() => undefined);
    const saved = window.localStorage.getItem(storageKey);
    if (!saved) {
      setLoading(false);
      return;
    }
    setConversationId(saved);
    request<ChatJob[]>('/chat/' + encodeURIComponent(saved))
      .then(setJobs)
      .catch((cause: Error) => {
        window.localStorage.removeItem(storageKey);
        setConversationId('');
        setError(cause.message);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!pending) return;
    const operationEpoch = conversationEpoch.current;
    let stopped = false;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const current = await request<ChatJob>('/chat/jobs/' + pending.id);
        if (!stopped && isCurrentConversation(operationEpoch, conversationEpoch.current)) {
          setJobs(previous => previous.map(job => job.id === current.id ? (job.status === 'cancelled' ? job : current) : job));
        }
      } catch (cause) {
        if (!stopped && isCurrentConversation(operationEpoch, conversationEpoch.current)) {
          setError((cause as Error).message);
        }
      } finally {
        polling = false;
      }
    };
    const timer = window.setInterval(poll, 1400);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [pending?.id]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [jobs.length, pending?.stage, pending?.status]);

  async function send(message = draft, fromVoice = false, spokenLanguage = voiceLanguage) {
    const clean = message.trim();
    if (!clean) return;
    const operationEpoch = conversationEpoch.current;
    if (clean.toLowerCase() === '/ayuda') {
      appendLocalResult('/ayuda', commandHelpText);
      return;
    }
    if (clean.toLowerCase() === '/new') {
      await startConversation();
      return;
    }
    if (clean.toLowerCase() === '/estado') {
      const startedAt = Date.now();
      setLocalCommandLabel('Comprobando servicios');
      setError('');
      let health: SystemHealth | null = null;
      try {
        health = await request<SystemHealth>('/health', { cache: 'no-store' });
      } catch {
        health = null;
      }
      if (!isCurrentConversation(operationEpoch, conversationEpoch.current)) return;
      appendLocalResult('/estado', healthDiagnostic(health), startedAt);
      setLocalCommandLabel('');
      return;
    }
    if (clean.toLowerCase() === '/videos') {
      const startedAt = Date.now();
      setLocalCommandLabel('Consultando vídeos');
      setError('');
      let videos: VideoSummary[] | null = null;
      try {
        videos = await request<VideoSummary[]>('/videos', { cache: 'no-store' });
      } catch {
        videos = null;
      }
      if (!isCurrentConversation(operationEpoch, conversationEpoch.current)) return;
      appendLocalResult('/videos', videoListText(videos), startedAt);
      setLocalCommandLabel('');
      return;
    }
    if (clean.toLowerCase().split(/\s+/, 1)[0] === '/reasoning') {
      const requested = clean.split(/\s+/)[1]?.toLowerCase();
      if (!requested) {
        appendLocalResult('/reasoning', `Fuerza de razonamiento actual: ${reasoningEffort}.\n\nUsa /reasoning <minimal|low|medium|high|xhigh|max> para cambiarla.`);
        return;
      }
      if (!reasoningEfforts.includes(requested as ReasoningEffort)) {
        appendLocalResult(clean, 'Nivel no válido. Usa /reasoning <minimal|low|medium|high|xhigh|max>.');
        return;
      }
      const nextEffort = requested as ReasoningEffort;
      const startedAt = Date.now();
      setLocalCommandLabel('Cambiando razonamiento');
      setError('');
      try {
        const status = await request<ReasoningStatus>('/reasoning', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ effort: nextEffort }),
        });
        if (!isCurrentConversation(operationEpoch, conversationEpoch.current)) return;
        const confirmed = statusReasoning(status, nextEffort);
        setReasoningEffort(confirmed);
        window.localStorage.setItem(reasoningStorageKey, confirmed);
        appendLocalResult(clean, `Fuerza de razonamiento cambiada a ${confirmed}. También se ha sincronizado con MedIA.`, startedAt);
      } catch (cause) {
        if (!isCurrentConversation(operationEpoch, conversationEpoch.current)) return;
        const message = (cause as Error).message;
        setError(message);
        appendLocalResult(clean, `No se pudo cambiar el razonamiento en MedIA: ${message}`, startedAt);
      } finally {
        if (isCurrentConversation(operationEpoch, conversationEpoch.current)) setLocalCommandLabel('');
      }
      return;
    }
    if (busy || (voiceBusy && !fromVoice)) return;
    const expanded = clean.toLowerCase().startsWith('/clips-todos ') ? clean : expandSlashCommand(clean);
    const targetConversationId = conversationId || window.crypto.randomUUID();
    if (!conversationId) {
      setConversationId(targetConversationId);
      window.localStorage.setItem(storageKey, targetConversationId);
    }
    setSubmitting(true);
    setError('');
    try {
      const created = await request<ChatJob>('/chat/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: expanded, conversation_id: targetConversationId, reasoning_effort: reasoningEffort,
          voice_language: spokenLanguage }),
      });
      if (!isCurrentConversation(operationEpoch, conversationEpoch.current)) return;
      setJobs(previous => [...previous, created]);
      setDraft('');
    } catch (cause) {
      if (isCurrentConversation(operationEpoch, conversationEpoch.current)) {
        setError((cause as Error).message);
      }
    } finally {
      if (isCurrentConversation(operationEpoch, conversationEpoch.current)) {
        setSubmitting(false);
        textarea.current?.focus();
      }
    }
  }

  async function stopGeneration() {
    if (!pending || stopping) return;
    const operationEpoch = conversationEpoch.current;
    setStopping(true);
    setError('');
    try {
      const cancelled = await request<ChatJob>('/chat/jobs/' + pending.id + '/cancel', { method: 'POST' });
      if (isCurrentConversation(operationEpoch, conversationEpoch.current)) {
        setJobs(previous => previous.map(job => job.id === cancelled.id ? cancelled : job));
      }
    } catch (cause) {
      if (isCurrentConversation(operationEpoch, conversationEpoch.current)) {
        setError((cause as Error).message);
      }
    } finally {
      if (isCurrentConversation(operationEpoch, conversationEpoch.current)) {
        setStopping(false);
        textarea.current?.focus();
      }
    }
  }

  async function startConversation() {
    if (!canStartNewConversation({ deleting, hasConversation: Boolean(conversationId), hasJobs: jobs.length > 0, submitting })) return;
    setError('');
    setDeleting(true);
    try {
      if (conversationId) {
        await request<DeleteChatResult>('/chat/' + encodeURIComponent(conversationId), { method: 'DELETE' });
      }
      conversationEpoch.current += 1;
      window.localStorage.removeItem(storageKey);
      setConversationId('');
      setJobs([]);
      setDraft('');
      setSubmitting(false);
      setStopping(false);
      setLocalCommandLabel('');
      setError('');
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setDeleting(false);
      textarea.current?.focus();
    }
  }

  return (
    <section className="hermes-chat" aria-label="Chat con MedIA">
      <div className="chat-surface">
        <header className="chat-toolbar">
          <span className={available ? 'hermes-status available' : 'hermes-status'}>
            {available ? `MedIA disponible${mode ? ` · ${mode}` : ''}` : 'MedIA no disponible'}
          </span>
          <button
            className="secondary"
            onClick={startConversation}
            disabled={!canStartNewConversation({ deleting, hasConversation: Boolean(conversationId), hasJobs: jobs.length > 0, submitting })}
          >
            {deleting ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}
            {deleting ? 'Borrando…' : 'Nuevo chat'}
          </button>
        </header>
        <div className="chat-messages" aria-live="polite" aria-busy={busy}>
          {loading ? (
            <div className="chat-empty"><LoaderCircle className="spin" /><p>Recuperando la conversación…</p></div>
          ) : !jobs.length ? (
            <div className="chat-empty">
              <span className="chat-empty-icon"><MessageSquare size={25} /></span>
              <h2>¿Qué quieres hacer?</h2>
              <p>Consulta los vídeos disponibles o pide ayuda para empezar.</p>
              <div className="chat-suggestions" aria-label="Sugerencias">
                {suggestions.map(suggestion => (
                  <button key={suggestion} onClick={() => { setDraft(suggestion); textarea.current?.focus(); }}>
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <ol className="message-list">
              {jobs.map(job => {
                const mediaList = getJobMedia(job);
                return (
                  <li key={job.id}>
                    <article className="chat-message user-message">
                      <span className="message-author">Tú</span>
                      <p>{job.message}</p>
                    </article>
                    <article className={job.status === 'failed' ? 'chat-message assistant-message failed' : 'chat-message assistant-message'}>
                      <span className="message-author"><Bot size={15} />MedIA</span>
                      {job.status === 'completed' ? (
                        <>
                          {renderFormattedText(job.answer || 'MedIA terminó sin devolver texto.')}
                          {mediaList.length > 0 && (
                            <div className="chat-media-list">
                              {mediaList.map((media, idx) => {
                                const isImage = media.kind === 'image' || media.name.endsWith('.jpg') || media.name.endsWith('.png');
                                const isVideo = !isImage && (media.kind === 'clip' || media.kind === 'video' || media.name.endsWith('.mp4') || media.name.endsWith('.webm'));
                                return (
                                  <div className={`chat-media-card ${isImage ? 'chat-image-card' : isVideo ? 'chat-video-card' : 'chat-audio-card'}`} key={media.url || idx}>
                                    <div className="chat-media-header">
                                      <div className="chat-media-title">
                                        {isImage ? <ImageIcon size={15} /> : isVideo ? <Film size={15} /> : <AudioLines size={15} />}
                                        <span title={media.name}>{media.name}</span>
                                      </div>
                                      <a
                                        href={media.url + '?download=true'}
                                        download={media.name}
                                        className="chat-media-download"
                                        title={`Descargar ${isImage ? 'imagen' : isVideo ? 'clip' : 'audio'}`}
                                      >
                                        <Download size={13} />
                                        <span>Descargar</span>
                                      </a>
                                    </div>
                                    {isImage ? (
                                      <div className="chat-image-wrapper">
                                        <img src={media.url} alt={media.name} loading="lazy" />
                                      </div>
                                    ) : isVideo ? (
                                      <div className="chat-video-wrapper">
                                        <video controls preload="metadata" src={media.url} />
                                      </div>
                                    ) : (
                                      <audio controls preload="metadata" src={media.url} />
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </>
                      ) : job.status === 'cancelled' ? (
                        <div className="chat-cancelled">
                          <p>Generación detenida.</p>
                          <button className="text-button" onClick={() => send(job.message)} disabled={busy}>Volver a intentar</button>
                        </div>
                      ) : job.status === 'failed' ? (
                        <div className="chat-failure">
                          <p><AlertCircle size={16} />{job.error ? mediaUiText(job.error) : 'MedIA no pudo completar la petición.'}</p>
                          <button className="text-button" onClick={() => send(job.message)} disabled={busy}>Volver a intentar</button>
                        </div>
                      ) : (
                        <div className="chat-progress" aria-label={`${mediaUiText(job.stage)}: ${job.progress || 0}%`}>
                          <p className="chat-working"><LoaderCircle className="spin" size={16} />{mediaUiText(job.stage)}</p>
                          {Boolean(job.events?.some(event => event.name)) && (
                            <div className="chat-activity" aria-live="polite">
                              <strong>Actividad en tiempo real</strong>
                              <ol>
                                {job.events!.filter(event => event.name).slice(-6).map((event, index) => (
                                  <li key={`${event.name}-${event.at}-${index}`} className={event.status === 'completed' ? 'completed' : 'running'}>
                                    {event.status === 'completed' ? <Check size={12} /> : <LoaderCircle className="spin" size={12} />}
                                    <span>{activityText(event)}</span>
                                    {typeof event.at === 'number' && <time>{event.at.toFixed(1)} s</time>}
                                  </li>
                                ))}
                              </ol>
                            </div>
                          )}
                          {typeof job.progress === 'number' && job.progress > 0 && (
                            <>
                              <div className="chat-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress}>
                                <span style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} />
                              </div>
                              <small>{job.progress}%</small>
                            </>
                          )}
                        </div>
                      )}
                      {job.status === 'completed' && (
                        <span className="message-complete"><Check size={13} />Completado{job.elapsed_seconds !== undefined ? ` en ${job.elapsed_seconds} s` : ''}</span>
                      )}
                    </article>
                  </li>
                );
              })}
            </ol>
          )}
          <div ref={end} />
        </div>

        {error && <div className="chat-error" role="alert"><AlertCircle size={17} />{mediaUiText(error)}</div>}
        <VoiceAvatar jobs={jobs} busy={busy} ready={!loading && available} conversationKey={conversationId}
          onBusyChange={setVoiceBusy} onModeChange={setVoiceLanguage}
          onTranscript={(text, language) => send(text, true, language)} />
        <form className="chat-composer" onSubmit={event => { event.preventDefault(); send(); }}>
          {commandMenuOpen && (
            <div className="slash-menu" id="hermes-command-menu" role="listbox" aria-label="Comandos disponibles">
              {filteredCommands.map((command, index) => (
                <button
                  type="button"
                  id={`hermes-command-${index}`}
                  role="option"
                  aria-selected={index === activeCommand}
                  className={index === activeCommand ? 'active' : ''}
                  key={command.name}
                  onMouseDown={event => event.preventDefault()}
                  onClick={() => selectCommand(command)}
                >
                  <strong>{command.name}</strong>
                  <span>{command.description}</span>
                  <small>{command.usage}</small>
                </button>
              ))}
            </div>
          )}
          {reasoningMenuOpen && (
            <div className="slash-menu" id="hermes-reasoning-menu" role="listbox" aria-label="Fuerza de razonamiento">
              {filteredReasoningOptions.map((option, index) => (
                <button
                  type="button"
                  id={`hermes-reasoning-${index}`}
                  role="option"
                  aria-selected={index === activeCommand}
                  className={index === activeCommand ? 'active' : ''}
                  key={option.value}
                  onMouseDown={event => event.preventDefault()}
                  onClick={() => selectReasoningOption(option.value)}
                >
                  <strong>{option.value}</strong>
                  <span>{option.description}</span>
                  <small>{option.value === reasoningEffort ? 'Actual' : ''}</small>
                </button>
              ))}
            </div>
          )}
          <div className="chat-composer-heading">
            <label htmlFor="hermes-prompt">Mensaje para MedIA</label>
            <small aria-label={`Fuerza de razonamiento actual: ${reasoningEffort}`}>
              Razonamiento: <strong>{reasoningEffort}</strong>
            </small>
          </div>
          <textarea
            ref={textarea}
            id="hermes-prompt"
            value={draft}
            onChange={event => {
              const nextDraft = event.target.value;
              setDraft(nextDraft);
              setActiveCommand(nextDraft.trim().toLowerCase() === '/reasoning'
                ? reasoningEfforts.indexOf(reasoningEffort)
                : 0);
              setCommandMenuDismissed(false);
            }}
            onKeyDown={event => {
              if (reasoningMenuOpen && event.key === 'ArrowDown') {
                event.preventDefault();
                setActiveCommand(index => (index + 1) % filteredReasoningOptions.length);
                return;
              }
              if (reasoningMenuOpen && event.key === 'ArrowUp') {
                event.preventDefault();
                setActiveCommand(index => (index - 1 + filteredReasoningOptions.length) % filteredReasoningOptions.length);
                return;
              }
              if (reasoningMenuOpen && event.key === 'Escape') {
                event.preventDefault();
                setCommandMenuDismissed(true);
                return;
              }
              if (reasoningMenuOpen && event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                selectReasoningOption(filteredReasoningOptions[activeCommand].value);
                return;
              }
              if (commandMenuOpen && event.key === 'ArrowDown') {
                event.preventDefault();
                setActiveCommand(index => (index + 1) % filteredCommands.length);
                return;
              }
              if (commandMenuOpen && event.key === 'ArrowUp') {
                event.preventDefault();
                setActiveCommand(index => (index - 1 + filteredCommands.length) % filteredCommands.length);
                return;
              }
              if (commandMenuOpen && event.key === 'Escape') {
                event.preventDefault();
                setCommandMenuDismissed(true);
                return;
              }
              if (commandMenuOpen && event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                selectCommand(filteredCommands[activeCommand]);
                return;
              }
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send();
              }
            }}
            placeholder="Escribe una petición sobre el proyecto…"
            maxLength={12000}
            rows={3}
            disabled={loading || busy || voiceBusy}
            aria-describedby="hermes-prompt-help"
            aria-autocomplete="list"
            aria-controls={reasoningMenuOpen ? 'hermes-reasoning-menu' : commandMenuOpen ? 'hermes-command-menu' : undefined}
            aria-expanded={autocompleteOpen}
            aria-activedescendant={reasoningMenuOpen
              ? `hermes-reasoning-${activeCommand}`
              : commandMenuOpen ? `hermes-command-${activeCommand}` : undefined}
          />
          <div className="chat-composer-footer">
            <p id="hermes-prompt-help">Escribe / para ver comandos · Enter para enviar</p>
            {pending ? (
              <button className="secondary stop-generation" type="button" onClick={stopGeneration} disabled={stopping}>
                {stopping ? <LoaderCircle className="spin" size={15} /> : <Square size={15} fill="currentColor" />}
                {stopping ? 'Deteniendo…' : 'Detener generación'}
              </button>
            ) : (
              <button className="primary" type="submit" disabled={loading || busy || voiceBusy || !draft.trim() || (!available && !localCommandRequested)}>
                {busy ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={17} />}
                {localCommandLabel || (busy ? 'MedIA está trabajando' : 'Enviar')}
              </button>
            )}
          </div>
        </form>
        <p className="chat-safety">MedIA puede modificar archivos mediante sus herramientas. Las acciones que requieran confirmación interactiva deben continuarse en MedIA Desktop.</p>
      </div>
    </section>
  );
}

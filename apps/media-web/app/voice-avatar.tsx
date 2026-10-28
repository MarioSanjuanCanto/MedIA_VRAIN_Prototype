'use client';

// Original SVG avatar and component: MIT, see voice-avatar.LICENSE.
import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Mic, Play, RotateCcw, Settings2, Sparkles, Square, Volume2, VolumeX, X } from 'lucide-react';
import { SpeechPauseDetector } from './voice-activity';

type Turn = { id: string; status: string; created_at: number; answer: string };
type Props = {
  jobs: Turn[]; busy: boolean; ready: boolean; conversationKey: string;
  onTranscript: (text: string, language: string) => Promise<void>; onBusyChange: (busy: boolean) => void;
  onModeChange: (language: string | null) => void;
};
type VoiceStatus = { asr_ready: boolean; voices: Record<string, boolean> };
type Phase = 'idle' | 'listening' | 'transcribing' | 'generating' | 'speaking';
type Reply = { url: string; spoken_text: string };
type AvatarColor = 'blue' | 'coral' | 'mint' | 'violet';
type HatStyle = 'none' | 'cap' | 'beanie';
type HandAccessory = 'none' | 'coffee' | 'water' | 'dumbbell' | 'clapper';
type AccessoryKey = 'glasses' | 'sunglasses' | 'headphones' | 'headset' | 'bowtie' | 'tie' | 'scarf' | 'medal';
type VoiceLanguage = 'es' | 'en' | 'ca';
type CustomizerPanel = 'style' | 'hand' | 'color';
type AvatarOptions = {
  hat: HatStyle; hand: HandAccessory; glasses: boolean; sunglasses: boolean; headphones: boolean; headset: boolean;
  bowtie: boolean; tie: boolean; scarf: boolean; medal: boolean; color: AvatarColor;
};
type VoicePersona = { id: string; name: string; language: VoiceLanguage; languageLabel: string; avatar: AvatarOptions };

const DEFAULT_AVATAR: AvatarOptions = {
  hat: 'none', hand: 'none', glasses: false, sunglasses: false, headphones: false, headset: false,
  bowtie: false, tie: false, scarf: false, medal: false, color: 'blue',
};
const AVATAR_COLORS: Record<AvatarColor, string> = { blue: '#244ccb', coral: '#d04f55', mint: '#178069', violet: '#7651c9' };
const VOICE_PERSONAS: VoicePersona[] = [
  { id: 'carlos', name: 'Carlos', language: 'es', languageLabel: 'Español',
    avatar: { ...DEFAULT_AVATAR, hat: 'cap', hand: 'coffee', glasses: true, headphones: true, color: 'blue' } },
  { id: 'lily', name: 'Lily', language: 'en', languageLabel: 'English',
    avatar: { ...DEFAULT_AVATAR, hat: 'beanie', hand: 'water', color: 'coral' } },
  { id: 'ona', name: 'Ona', language: 'ca', languageLabel: 'Català',
    avatar: { ...DEFAULT_AVATAR, headset: true, scarf: true, color: 'mint' } },
];
const VOICE_FLAGS: Record<VoiceLanguage, string> = { es: 'España', en: 'Inglaterra', ca: 'Cataluña' };

async function voiceRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/voice/' + url, init);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'No se pudo completar la petición de voz.');
  return body as T;
}

export default function VoiceAvatar({ jobs, busy, ready, conversationKey, onTranscript, onBusyChange, onModeChange }: Props) {
  const [enabled, setEnabled] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [language, setLanguage] = useState<VoiceLanguage>('es');
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');
  const [reply, setReply] = useState<Reply | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [customizing, setCustomizing] = useState(false);
  const [customizerPanel, setCustomizerPanel] = useState<CustomizerPanel>('style');
  const [avatar, setAvatar] = useState<AvatarOptions>(DEFAULT_AVATAR);
  const [customizationReady, setCustomizationReady] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const epoch = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const activation = useRef(Infinity);
  const spoken = useRef(new Set<string>());
  const context = useRef<AudioContext | null>(null);
  const source = useRef<MediaElementAudioSourceNode | null>(null);
  const animation = useRef(0);
  const meterConnection = useRef<{ node: AudioNode; analyser: AnalyserNode } | null>(null);
  const waitingVoice = useRef(false);
  const waitingSequence = useRef(0);
  const suppressedWaiting = useRef('');
  const pendingTurn = jobs.find(job => job.status === 'queued' || job.status === 'running');
  const latest = [...jobs].reverse().find(job => job.status === 'completed' && !job.id.startsWith('local-'));
  const working = phase === 'transcribing' || phase === 'generating';

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem('media-avatar-options');
      if (saved) {
        const stored = JSON.parse(saved) as Omit<Partial<AvatarOptions>, 'hat' | 'hand'> & { hat?: HatStyle | boolean; hand?: HandAccessory; beard?: boolean; moustache?: boolean; coffee?: boolean };
        const savedHat = stored.hat === true ? 'cap' : stored.hat === false ? 'none' : stored.hat;
        const hat: HatStyle = savedHat === 'cap' || savedHat === 'beanie' ? savedHat : 'none';
        const hand: HandAccessory = stored.hand === 'coffee' || stored.hand === 'water' || stored.hand === 'dumbbell' || stored.hand === 'clapper'
          ? stored.hand : stored.coffee ? 'coffee' : 'none';
        const bowtie = stored.bowtie ?? false;
        const tie = !bowtie && (stored.tie ?? false);
        const scarf = !bowtie && !tie && (stored.scarf ?? false);
        const medal = !bowtie && !tie && !scarf && (stored.medal ?? false);
        setAvatar({ hat, hand, glasses: stored.glasses ?? false, sunglasses: stored.sunglasses ?? false,
          headphones: stored.headphones ?? false, headset: stored.headset ?? false, bowtie,
          tie, scarf, medal, color: stored.color ?? 'blue' });
      }
      const savedLanguage = window.localStorage.getItem('media-avatar-language');
      if (savedLanguage === 'es' || savedLanguage === 'en' || savedLanguage === 'ca') setLanguage(savedLanguage);
    } catch { /* Keep defaults when storage is unavailable or invalid. */ }
    setCustomizationReady(true);
  }, []);
  useEffect(() => {
    if (!customizationReady) return;
    window.localStorage.setItem('media-avatar-options', JSON.stringify(avatar));
    window.localStorage.setItem('media-avatar-language', language);
  }, [avatar, language, customizationReady]);

  function toggleAccessory(accessory: AccessoryKey) {
    setAvatar(current => {
      const enabled = !current[accessory];
      const next = { ...current, [accessory]: enabled };
      if (!enabled) return next;
      if (accessory === 'glasses') next.sunglasses = false;
      if (accessory === 'sunglasses') next.glasses = false;
      if (accessory === 'headphones') next.headset = false;
      if (accessory === 'headset') next.headphones = false;
      if (accessory === 'bowtie' || accessory === 'tie' || accessory === 'scarf' || accessory === 'medal') {
        next.bowtie = accessory === 'bowtie'; next.tie = accessory === 'tie';
        next.scarf = accessory === 'scarf'; next.medal = accessory === 'medal';
      }
      return next;
    });
  }
  function surpriseAvatar() {
    const colors: AvatarColor[] = ['blue', 'coral', 'mint', 'violet'];
    const hats: HatStyle[] = ['none', 'cap', 'beanie'];
    const hands: HandAccessory[] = ['none', 'coffee', 'water', 'dumbbell', 'clapper'];
    const eyewear = Math.floor(Math.random() * 3);
    const audioAccessory = Math.floor(Math.random() * 3);
    const neckAccessory = Math.floor(Math.random() * 5);
    setAvatar({
      hat: hats[Math.floor(Math.random() * hats.length)], hand: hands[Math.floor(Math.random() * hands.length)],
      glasses: eyewear === 1, sunglasses: eyewear === 2, headphones: audioAccessory === 1, headset: audioAccessory === 2,
      bowtie: neckAccessory === 1, tie: neckAccessory === 2, scarf: neckAccessory === 3, medal: neckAccessory === 4,
      color: colors[Math.floor(Math.random() * colors.length)],
    });
  }
  function applyPersona(persona: VoicePersona) {
    stopAll(); setReply(null); setError(''); setHint('');
    setLanguage(persona.language); setAvatar(persona.avatar);
  }

  function stopMeter() {
    cancelAnimationFrame(animation.current);
    if (meterConnection.current) {
      meterConnection.current.node.disconnect(meterConnection.current.analyser);
      meterConnection.current = null;
    }
    setLevel(0);
  }
  function meter(node: AudioNode, observe?: (rms: number) => void) {
    if (!context.current) return;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const analyser = context.current.createAnalyser();
    analyser.fftSize = 256;
    stopMeter();
    node.connect(analyser); meterConnection.current = { node, analyser };
    const values = new Uint8Array(analyser.fftSize);
    const draw = () => {
      analyser.getByteTimeDomainData(values);
      const rms = Math.sqrt(values.reduce((sum, n) => sum + ((n - 128) / 128) ** 2, 0) / values.length);
      if (!reduceMotion) setLevel(Math.min(1, rms * 5));
      observe?.(rms);
      if (meterConnection.current?.analyser !== analyser) return;
      animation.current = requestAnimationFrame(draw);
    };
    draw();
  }
  function releaseMicrophone() {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
  }
  function stopAll() {
    waitingVoice.current = false;
    epoch.current += 1;
    controller.current?.abort();
    if (recorder.current?.state === 'recording') recorder.current.stop();
    releaseMicrophone();
    audio.current?.pause();
    stopMeter();
    setPhase('idle');
  }

  useEffect(() => { onBusyChange(phase === 'listening' || phase === 'transcribing'); }, [phase, onBusyChange]);
  useEffect(() => { onModeChange(enabled ? language : null); }, [enabled, language, onModeChange]);
  useEffect(() => {
    stopAll(); setReply(null); setError(''); setHint(''); spoken.current.clear();
    activation.current = Date.now() / 1000;
    return () => {
      epoch.current += 1; controller.current?.abort();
      if (recorder.current?.state === 'recording') recorder.current.stop();
      releaseMicrophone(); audio.current?.pause(); cancelAnimationFrame(animation.current);
    };
  }, [conversationKey]);
  useEffect(() => () => { void context.current?.close(); }, []);
  useEffect(() => {
    if (busy) { stopAll(); setReply(null); setHint(''); }
  }, [busy]);
  useEffect(() => {
    if (!enabled || !ready || !busy || !pendingTurn) return;
    let cancelled = false;
    const first = waitingSequence.current++ % 4;
    const timers = [12000, 45000, 90000].map((delay, index) => window.setTimeout(() => {
      if (cancelled || suppressedWaiting.current === pendingTurn.id || (audio.current && !audio.current.paused)) return;
      void speak('', false, (first + index) % 4);
    }, delay));
    return () => {
      cancelled = true;
      timers.forEach(window.clearTimeout);
      if (waitingVoice.current) { stopAll(); setReply(null); }
    };
  }, [enabled, ready, busy, pendingTurn?.id, language, conversationKey]);
  useEffect(() => {
    if (!enabled || !ready || busy || !latest || latest.created_at < activation.current || spoken.current.has(latest.id)) return;
    spoken.current.add(latest.id);
    void speak(latest.id);
  }, [latest?.id, latest?.status, enabled, ready, busy]);

  async function toggle() {
    if (enabled) { stopAll(); setEnabled(false); setReply(null); return; }
    setEnabled(true); setError(''); activation.current = Date.now() / 1000;
    const current = ++epoch.current;
    try {
      if (!context.current) context.current = new AudioContext();
      await context.current.resume();
      const next = await voiceRequest<VoiceStatus>('status');
      if (current === epoch.current) {
        setStatus(next);
        if (next.voices[language]) await speak('', true);
      }
    } catch (cause) { if (current === epoch.current) setError((cause as Error).message); }
  }

  async function play() {
    if (!audio.current) return;
    try {
      await context.current?.resume();
      await audio.current.play(); setHint('');
    } catch { setPhase('idle'); setHint('Pulsa «Escuchar» para reproducir la voz.'); }
  }
  async function speak(jobId: string, greeting = false, filler?: number) {
    stopAll(); setError(''); setReply(null); setExpanded(true); setPhase('generating');
    waitingVoice.current = filler !== undefined;
    const current = epoch.current;
    const abort = new AbortController(); controller.current = abort;
    try {
      const next = await voiceRequest<Reply>(filler !== undefined ? 'filler' : greeting ? 'greeting' : 'reply', { method: 'POST', signal: abort.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId, language, index: filler }) });
      if (current !== epoch.current) return;
      setReply(next);
      if (audio.current) { audio.current.src = next.url; await play(); }
    } catch (cause) {
      if (current === epoch.current) {
        if (filler === undefined) setError((cause as Error).message);
        setPhase('idle');
      }
    }
  }

  async function record() {
    if (phase === 'listening') {
      if (recorder.current?.state === 'recording') recorder.current.stop();
      else stopAll();
      return;
    }
    stopAll(); setReply(null); setError(''); setHint('');
    const current = epoch.current;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('El micrófono necesita un navegador compatible y HTTPS o localhost.'); return;
    }
    // Mark as listening before requesting permission, so a second click cannot start another capture.
    setPhase('listening'); setSeconds(0);
    try {
      const capture = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
      if (current !== epoch.current) { capture.getTracks().forEach(track => track.stop()); return; }
      stream.current = capture;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      const rec = new MediaRecorder(capture, mime ? { mimeType: mime } : undefined);
      recorder.current = rec;
      const chunks: BlobPart[] = [];
      rec.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      rec.onerror = () => { if (current === epoch.current) { stopAll(); setError('No se pudo grabar el audio.'); } };
      rec.onstop = async () => {
        if (current !== epoch.current) return;
        releaseMicrophone(); stopMeter();
        setPhase('transcribing');
        const form = new FormData();
        form.append('file', new Blob(chunks, { type: rec.mimeType }), 'microphone');
        const abort = new AbortController(); controller.current = abort;
        try {
          const result = await voiceRequest<{ text: string }>('transcribe?language=' + language,
            { method: 'POST', body: form, signal: abort.signal });
          if (current !== epoch.current) return;
          if (result.text.trim()) {
            setHint('He oído: «' + result.text + '»');
            await onTranscript(result.text, language);
          }
          else setHint('No se ha detectado voz. Acércate al micrófono y vuelve a intentarlo.');
        } catch (cause) { if (current === epoch.current) setError((cause as Error).message); }
        finally { if (current === epoch.current) setPhase('idle'); }
      };
      if (!context.current) context.current = new AudioContext();
      await context.current.resume();
      if (current !== epoch.current) { releaseMicrophone(); return; }
      rec.start(250);
      const detector = new SpeechPauseDetector(performance.now());
      meter(context.current.createMediaStreamSource(capture), rms => {
        if (rec.state !== 'recording' || current !== epoch.current) return;
        const decision = detector.observe(rms, performance.now());
        if (decision === 'pause') rec.stop();
        if (decision === 'empty') {
          stopAll(); setHint('No he oído ninguna petición. Pulsa Hablar para intentarlo de nuevo.');
        }
      });
      const start = Date.now();
      timer.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - start) / 1000); setSeconds(elapsed);
        if (elapsed >= 59 && rec.state === 'recording') rec.stop();
      }, 250);
    } catch (cause) {
      if (current === epoch.current) { releaseMicrophone(); stopMeter(); setPhase('idle');
        setError((cause as Error).name === 'NotAllowedError' ? 'Permite el acceso al micrófono en tu navegador.' : 'No se pudo abrir el micrófono.'); }
    }
  }

  const label = !enabled ? 'Habla con MedIA' : busy ? 'Estoy consultando tus vídeos…'
    : phase === 'listening' ? `Te escucho · ${seconds} s` : phase === 'transcribing' ? 'Transcribiendo tu petición…'
    : phase === 'generating' ? 'Preparando una respuesta breve…' : phase === 'speaking' ? 'MedIA está hablando' : '¿En qué puedo ayudarte?';
  return <aside className={`voice-companion phase-${phase} ${enabled ? 'enabled' : ''} ${expanded ? 'expanded' : ''}`} aria-label="Asistente de voz">
    <button type="button" className="voice-avatar-launcher" aria-label={expanded ? 'Ocultar asistente de voz' : 'Abrir asistente de voz'}
      aria-expanded={expanded} onClick={() => {
        setExpanded(!expanded);
        if (!expanded && !enabled) void toggle();
      }}>
    <svg className="voice-avatar" viewBox="0 0 84 84" aria-hidden="true" style={{ color: AVATAR_COLORS[avatar.color] }}>
      <circle className="avatar-halo" cx="42" cy="42" r="40" fill="var(--tint)" />
      <g className="avatar-character">
      <path className="avatar-body" d="M20 72c2-13 12-17 22-17s20 4 22 17" fill="currentColor" opacity=".2" />
      {avatar.headphones && <g className="avatar-accessory avatar-headphones">
        <path d="M16 38v-5c0-16 10-24 26-24s26 8 26 24v5" />
        <rect x="12" y="35" width="9" height="18" rx="4" /><rect x="63" y="35" width="9" height="18" rx="4" />
      </g>}
      <rect className="avatar-head" x="19" y="18" width="46" height="42" rx="17" fill="currentColor" />
      <rect className="avatar-face" x="26" y="26" width="32" height="25" rx="11" fill="white" />
      <ellipse className="avatar-eye avatar-eye-left" cx="35" cy="35" rx="2.3" ry={phase === 'speaking' ? 2.8 : 2.3} fill="currentColor" />
      <ellipse className="avatar-eye avatar-eye-right" cx="49" cy="35" rx="2.3" ry={phase === 'speaking' ? 2.8 : 2.3} fill="currentColor" />
      <rect className="avatar-mouth" x="36" y={43 - level * 3} width="12" height={2 + level * 7} rx="2" fill="currentColor" />
      {avatar.glasses && <g className="avatar-accessory avatar-glasses">
        <rect x="29" y="30" width="12" height="10" rx="4" /><rect x="43" y="30" width="12" height="10" rx="4" /><path d="M41 34h2" />
      </g>}
      {avatar.sunglasses && <g className="avatar-accessory avatar-sunglasses">
        <path d="M28 30h14l-1 8c-1 4-11 4-12-1Zm28 0H42l1 8c1 4 11 4 12-1Z" /><path d="M41 33h2M28 31l-4-2m32 2 4-2" />
      </g>}
      {avatar.headset && <g className="avatar-accessory avatar-headset">
        <g transform="translate(84 -3) scale(-1 1)">
          <rect className="avatar-headset-earpiece" x="63" y="36" width="7" height="13" rx="3.5" />
          <path className="avatar-headset-shine" d="M66 39v5" />
          <path className="avatar-headset-boom" d="M65 48c-1 4-4 6-9 6h-2" />
          <circle className="avatar-headset-mic" cx="53" cy="54" r="2.4" />
        </g>
      </g>}
      {avatar.hat === 'none' && <g className="avatar-antenna"><path d="M42 18V12" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        <circle cx="42" cy="10" r="3" fill={phase === 'listening' ? '#b3261e' : 'currentColor'} /></g>}
      {avatar.hat === 'cap' && <g className="avatar-accessory avatar-hat avatar-cap">
        <path d="M23 20c1-10 8-16 19-16s18 6 19 16Z" /><rect x="20" y="17" width="44" height="7" rx="3.5" /><circle cx="42" cy="4" r="4" />
      </g>}
      {avatar.hat === 'beanie' && <g className="avatar-accessory avatar-hat avatar-beanie">
        <path d="M25 19C25 8 32 2 42 2s17 6 17 17Z" /><rect x="22" y="16" width="40" height="8" rx="4" /><circle cx="42" cy="2" r="4" />
      </g>}
      {avatar.bowtie && <g className="avatar-accessory avatar-bowtie">
        <path d="M41 64c-5-5-11-5-11 1s6 6 11 1v-2Zm2 0c5-5 11-5 11 1s-6 6-11 1v-2Z" /><circle cx="42" cy="65" r="3" />
      </g>}
      {avatar.tie && <g className="avatar-accessory avatar-tie">
        <path d="m38 58 4-3 4 3-2 5 3 10-5 5-5-5 3-10Z" />
      </g>}
      {avatar.scarf && <g className="avatar-accessory avatar-scarf">
        <path d="M24 54c9 5 27 5 36 0l2 8c-11 5-29 5-40 0Z" /><path d="m51 63 8-1 2 14-8-1Z" />
      </g>}
      {avatar.medal && <g className="avatar-accessory avatar-medal">
        <path d="m35 59 7 9 7-9" /><circle cx="42" cy="69" r="5" /><path d="m42 66.5.9 1.8 2 .3-1.5 1.4.4 2-1.8-.9-1.8.9.4-2-1.5-1.4 2-.3Z" />
      </g>}
      {avatar.hand === 'coffee' && <g className="avatar-accessory avatar-hand-item avatar-coffee">
        <path className="avatar-coffee-arm" d="M57 65c4 1 7 3 9 6" />
        <path className="avatar-coffee-cup" d="m60 55 15 1-2 19H63Z" />
        <rect className="avatar-coffee-lid" x="58" y="52" width="19" height="5" rx="2.5" />
        <circle className="avatar-coffee-badge" cx="67.5" cy="64" r="4.2" />
        <path className="avatar-coffee-star" d="m67.5 60.8 1 2 2.2.3-1.6 1.5.4 2.2-2-1-2 1 .4-2.2-1.6-1.5 2.2-.3Z" />
        <g className="avatar-coffee-steam"><path d="M64 50c-2-2 2-3 0-5" /><path d="M70 50c-2-2 2-3 0-5" /></g>
      </g>}
      {avatar.hand === 'water' && <g className="avatar-accessory avatar-hand-item avatar-water">
        <path className="avatar-hand-arm" d="M57 65c4 1 7 3 9 6" /><path className="avatar-water-bottle" d="M64 55h8l2 4v16H62V59Z" />
        <rect className="avatar-water-cap" x="65" y="51" width="6" height="5" rx="1" /><path className="avatar-water-label" d="M63 63h10v7H63Z" /><path className="avatar-water-wave" d="M64 67c2-2 4 2 6 0 1-1 2-1 3 0" />
      </g>}
      {avatar.hand === 'dumbbell' && <g className="avatar-accessory avatar-hand-item avatar-dumbbell">
        <path className="avatar-hand-arm" d="M56 66c5 0 8 0 11 2" /><path className="avatar-dumbbell-bar" d="M59 61h17" />
        <rect x="57" y="56" width="5" height="11" rx="2" /><rect x="74" y="56" width="5" height="11" rx="2" />
      </g>}
      {avatar.hand === 'clapper' && <g className="avatar-accessory avatar-hand-item avatar-clapper">
        <path className="avatar-hand-arm" d="M56 67c4 0 7 1 10 4" /><rect className="avatar-clapper-board" x="58" y="57" width="19" height="16" rx="2" />
        <path className="avatar-clapper-top" d="m58 57 3-7 18 3-2 6Z" /><path className="avatar-clapper-stripes" d="m64 51-3 6m10-5-3 6m9-5-3 6" /><path className="avatar-clapper-lines" d="M62 63h11m-11 4h8" />
      </g>}
      </g>
    </svg>
    </button>
    <div className="voice-companion-body" hidden={!expanded}>
      <div className="voice-companion-heading"><strong aria-live="polite">{label}</strong>
        <button type="button" className={`voice-toggle avatar-customize-toggle ${customizing ? 'active' : ''}`} aria-label="Personalizar avatar"
          aria-expanded={customizing} onClick={() => setCustomizing(value => !value)}><Settings2 size={15} /><span>Personalizar</span></button>
        <button type="button" className="voice-toggle voice-close" aria-label="Cerrar bocadillo" onClick={() => setExpanded(false)}><X size={15} /></button>
        <button type="button" className="voice-toggle" onClick={toggle} aria-pressed={enabled}>
          {enabled ? <VolumeX size={15} /> : <Volume2 size={15} />}{enabled ? 'Desactivar voz' : 'Activar voz'}
        </button>
      </div>
      {customizing && <section className="avatar-customizer" aria-label="Personalización del avatar">
        <div className="avatar-customizer-title"><div><strong>Personaliza a MedIA</strong><span>Elige una voz y ajusta su aspecto</span></div><small>Guardado automático</small></div>
        <div className="avatar-wardrobe-group"><span>Personajes de voz</span><div className="avatar-persona-options">
          {VOICE_PERSONAS.map(persona => <button type="button" key={persona.id} className={language === persona.language ? 'selected' : ''}
            aria-pressed={language === persona.language} disabled={busy || working || phase === 'listening'} onClick={() => applyPersona(persona)}>
            <i className="voice-persona-medallion" style={{ background: AVATAR_COLORS[persona.avatar.color] }}>
              <span className={`voice-persona-flag flag-${persona.language}`} role="img" aria-label={`Bandera de ${VOICE_FLAGS[persona.language]}`} />
            </i>
            <span><strong>{persona.name}</strong><small>{persona.languageLabel}</small></span>
          </button>)}
        </div></div>
        <div className="avatar-customizer-tabs" role="tablist" aria-label="Opciones de aspecto">
          {([['style', 'Estilo'], ['hand', 'En la mano'], ['color', 'Color']] as const).map(([panel, name]) =>
            <button type="button" key={panel} role="tab" aria-selected={customizerPanel === panel}
              className={customizerPanel === panel ? 'selected' : ''} onClick={() => setCustomizerPanel(panel)}>{name}</button>)}
        </div>
        <div className="avatar-customizer-panel" role="tabpanel">
          {customizerPanel === 'style' && <>
            <div className="avatar-wardrobe-group"><span>Sombrero</span><div className="avatar-hat-options">
              {([['none', 'Sin gorro'], ['cap', '🧢 Gorra'], ['beanie', '🧶 Lana']] as const).map(([hat, name]) =>
                <button type="button" key={hat} className={avatar.hat === hat ? 'selected' : ''} aria-pressed={avatar.hat === hat}
                  onClick={() => setAvatar(current => ({ ...current, hat }))}>{name}</button>)}
            </div></div>
            <div className="avatar-wardrobe-group"><span>Complementos</span><div className="avatar-accessory-options">
              {([
                ['glasses', '👓', 'Gafas'], ['sunglasses', '🕶️', 'Gafas de sol'], ['headphones', '🎧', 'Auriculares'],
                ['headset', '🎙️', 'Micrófono'], ['bowtie', '🎀', 'Pajarita'], ['tie', '👔', 'Corbata'],
                ['scarf', '🧣', 'Bufanda'], ['medal', '🏅', 'Medalla'],
              ] as const).map(([key, icon, name]) => <button type="button" key={key} className={avatar[key] ? 'selected' : ''}
                aria-pressed={avatar[key]} onClick={() => toggleAccessory(key)}><span aria-hidden="true">{icon}</span>{name}</button>)}
            </div></div>
          </>}
          {customizerPanel === 'hand' && <div className="avatar-wardrobe-group avatar-panel-first-group"><span>Elige un objeto</span><div className="avatar-hand-options">
            {([['none', 'Manos libres'], ['coffee', '☕ Starbucks'], ['water', '💧 Agua'], ['dumbbell', '🏋️ Mancuerna'], ['clapper', '🎬 Claqueta']] as const)
              .map(([hand, name]) => <button type="button" key={hand} className={avatar.hand === hand ? 'selected' : ''}
                aria-pressed={avatar.hand === hand} onClick={() => setAvatar(current => ({ ...current, hand }))}>{name}</button>)}
          </div></div>}
          {customizerPanel === 'color' && <div className="avatar-color-row"><span>Color del robot</span><div className="avatar-colors">
            {(Object.keys(AVATAR_COLORS) as AvatarColor[]).map(color => <button type="button" key={color}
              className={avatar.color === color ? 'selected' : ''} style={{ background: AVATAR_COLORS[color] }}
              aria-label={`Color ${color}`} aria-pressed={avatar.color === color} onClick={() => setAvatar(current => ({ ...current, color }))} />)}
          </div></div>}
        </div>
        <div className="avatar-customizer-actions">
          <button type="button" onClick={surpriseAvatar}><Sparkles size={14} />Sorpréndeme</button>
          <button type="button" onClick={() => setAvatar(DEFAULT_AVATAR)}><RotateCcw size={14} />Restablecer</button>
        </div>
      </section>}
      {enabled ? <>
        <div className="voice-actions">
          <select aria-label="Idioma" value={language} disabled={busy || working || phase === 'listening'} onChange={event => { stopAll(); setReply(null); setLanguage(event.target.value as VoiceLanguage); }}>
            <option value="es">Español</option><option value="ca">Català</option><option value="en">English</option>
          </select>
          <button type="button" className="secondary" onClick={record}
            disabled={!ready || busy || working || !status?.asr_ready}>
            {phase === 'listening' ? <Square size={14} /> : phase === 'transcribing' ? <LoaderCircle size={14} className="spin" /> : <Mic size={14} />}
            {phase === 'listening' ? 'Terminar grabación' : 'Hablar'}
          </button>
          {phase === 'speaking' || phase === 'generating' ? <button type="button" className="secondary" onClick={() => {
            suppressedWaiting.current = pendingTurn?.id || ''; stopAll();
          }}><Square size={14} />Detener voz</button>
            : (latest || reply) && <button type="button" className="secondary" disabled={busy || !ready || phase === 'listening' || working || !status?.voices[language]}
              onClick={() => reply ? void play() : latest && void speak(latest.id)}><Play size={14} />Escuchar</button>}
        </div>
        {status && !status.asr_ready && <p role="status">El reconocimiento de voz local aún no está preparado. Puedes seguir escribiendo.</p>}
        {status && !status.voices[language] && <p role="status">Falta la voz de este idioma. Puedes usar el chat escrito.</p>}
        {reply && <p className="voice-caption">{reply.spoken_text}</p>}
        {hint && <p role="status">{hint}</p>}
        {error && <p className="voice-error" role="alert">{error}</p>}
        <small>{phase === 'listening' ? 'Haz una pausa y enviaré tu petición automáticamente.' : 'Whisper y Piper locales · Voz breve, respuesta completa en el chat.'}</small>
      </> : <p>Dicta tu petición y escucha una respuesta breve.</p>}
    </div>
    <audio ref={audio} preload="none" onPlay={() => {
      setPhase('speaking');
      if (audio.current && context.current) {
        if (!source.current) { source.current = context.current.createMediaElementSource(audio.current); source.current.connect(context.current.destination); }
        meter(source.current);
      }
    }} onEnded={() => { stopMeter(); setPhase('idle'); }}
      onPause={() => { stopMeter(); setPhase(previous => previous === 'speaking' ? 'idle' : previous); }}
      onError={() => { stopMeter(); setPhase('idle'); setError('No se pudo reproducir la voz. Vuelve a intentarlo.'); }} />
  </aside>;
}

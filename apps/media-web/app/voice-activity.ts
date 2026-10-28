/** Energy-based end-of-turn detection, independent of avatar animation. */
export class SpeechPauseDetector {
  private started: number;
  private previous: number;
  private lastVoice: number;
  private voicedMs = 0;
  constructor(now: number) { this.started = this.previous = this.lastVoice = now; }
  observe(rms: number, now: number): 'continue' | 'pause' | 'empty' {
    const elapsed = Math.min(200, Math.max(0, now - this.previous));
    this.previous = now;
    if (rms >= 0.018) { this.voicedMs += elapsed; this.lastVoice = now; }
    if (this.voicedMs >= 250 && now - this.lastVoice >= 1400) return 'pause';
    if (this.voicedMs < 250 && now - this.started >= 10000) return 'empty';
    return 'continue';
  }
}

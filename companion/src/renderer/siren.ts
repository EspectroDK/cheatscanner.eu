// A short two-tone siren made with the Web Audio API (no sound file). About 2.4 seconds.

let ctx: AudioContext | null = null;

export function playSiren(volume = 0.22): void {
  try {
    ctx ??= new AudioContext();
    void ctx.resume();
    const t0 = ctx.currentTime + 0.02;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(volume, t0 + 0.05);
    gain.gain.setValueAtTime(volume, t0 + 2.3);
    gain.gain.linearRampToValueAtTime(0, t0 + 2.4);
    gain.connect(ctx.destination);
    const osc = ctx.createOscillator();
    osc.type = "sawtooth";
    // Up and down four times, like an emergency siren.
    for (let i = 0; i < 4; i++) {
      osc.frequency.setValueAtTime(620, t0 + i * 0.6);
      osc.frequency.linearRampToValueAtTime(1180, t0 + i * 0.6 + 0.3);
      osc.frequency.linearRampToValueAtTime(620, t0 + i * 0.6 + 0.6);
    }
    const soften = ctx.createBiquadFilter();
    soften.type = "lowpass";
    soften.frequency.value = 2400;
    osc.connect(soften).connect(gain);
    osc.start(t0);
    osc.stop(t0 + 2.45);
  } catch {
    /* no audio device */
  }
}

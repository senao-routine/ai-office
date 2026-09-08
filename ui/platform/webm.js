const WEBM_TYPES = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];

export function webmType(canvas) {
  if (typeof canvas?.captureStream !== "function" || typeof MediaRecorder === "undefined"
      || typeof MediaRecorder.isTypeSupported !== "function") return null;
  return WEBM_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) || null;
}

/** The replay owns elapsed time. Stop only after its final frame has reached the canvas. */
export function recordWebM(canvas, { done, failed }) {
  const mimeType = webmType(canvas);
  if (!mimeType) throw new Error("WebM unavailable");
  const stream = canvas.captureStream(30);
  let recorder, cancelled = false, chunks = [];
  const release = () => stream.getTracks().forEach((track) => track.stop());
  try {
    recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 6000000 });
    recorder.ondataavailable = (event) => { if (!cancelled && event.data.size) chunks.push(event.data); };
    recorder.onerror = () => {
      cancelled = true; chunks = []; release(); failed();
    };
    recorder.onstop = () => {
      release();
      if (cancelled) return;
      if (!chunks.length) { failed(); return; }
      const url = URL.createObjectURL(new Blob(chunks, { type: mimeType }));
      const link = document.createElement("a");
      link.href = url; link.download = "ai-office-replay.webm";
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      chunks = []; done();
    };
    recorder.start();
  } catch (err) { release(); throw err; }
  return {
    stop() { if (recorder.state !== "inactive") recorder.stop(); },
    cancel() {
      cancelled = true; chunks = [];
      if (recorder.state !== "inactive") recorder.stop();
      release();
    },
  };
}

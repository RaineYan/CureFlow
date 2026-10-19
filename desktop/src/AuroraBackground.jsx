import { useEffect, useRef } from "react";
import { paintAurora } from "./aurora-renderer.mjs";

// One persistent canvas, independent of analysis state and page navigation.
export default function AuroraBackground() {
  const canvasRef = useRef(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas.getContext("2d");
    if (!context) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0, lastFrame = 0, elapsed = 0, previousTime = 0;
    let width = 0, height = 0, disposed = false;

    function paint(time) {
      paintAurora(context, width, height, time);
    }
    function tick(now) {
      if (disposed || document.hidden || motion.matches) return;
      // Limit work to 30 fps; preserve the phase across background pauses.
      if (now - lastFrame >= 1000 / 30) {
        if (previousTime) elapsed += Math.min((now - previousTime) / 1000, .1);
        previousTime = now;
        lastFrame = now;
        if (width && height) paint(elapsed);
      }
      frame = window.requestAnimationFrame(tick);
    }
    function resume() {
      window.cancelAnimationFrame(frame);
      previousTime = 0;
      if (motion.matches && width && height) paint(2);
      else if (!document.hidden) frame = window.requestAnimationFrame(tick);
    }
    const resize = new ResizeObserver(() => {
      const box = canvas.getBoundingClientRect();
      width = box.width;
      height = box.height;
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      if (width && height) paint(motion.matches ? 2 : elapsed);
    });
    resize.observe(canvas);
    document.addEventListener("visibilitychange", resume);
    motion.addEventListener("change", resume);
    resume();
    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      resize.disconnect();
      document.removeEventListener("visibilitychange", resume);
      motion.removeEventListener("change", resume);
    };
  }, []);
  return <div className="aurora-background" aria-hidden="true"><canvas ref={canvasRef} className="aurora-ribbons" /></div>;
}

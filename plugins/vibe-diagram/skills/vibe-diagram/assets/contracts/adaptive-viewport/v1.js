(() => {
  "use strict";
  const allowed = new Set(["fit", "0.75", "0.9", "1"]);
  const reset = (canvas) => {
    canvas.style.removeProperty("--diagram-scale");
    canvas.removeAttribute("data-diagram-scaled");
  };
  const apply = (canvas, requested = "fit") => {
    reset(canvas);
    if (!allowed.has(requested)) return false;
    const stage = canvas.querySelector(":scope > [data-diagram-stage]");
    if (!stage || !canvas.clientWidth || !stage.scrollWidth) return false;
    const scale = requested === "fit"
      ? Math.min(1, canvas.clientWidth / stage.scrollWidth)
      : Number(requested);
    if (!Number.isFinite(scale) || scale < 0.75 || scale > 1) return false;
    canvas.style.setProperty("--diagram-scale", String(scale));
    canvas.setAttribute("data-diagram-scaled", "true");
    return true;
  };
  const reflect = (controls, requested, applied) => {
    controls.querySelectorAll("[data-diagram-zoom-control]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.diagramZoomControl === requested && applied)
      );
    });
    const status = controls.querySelector("[data-diagram-zoom-status]");
    if (status) status.value = applied ? (requested === "fit" ? "Fit width" : `${Number(requested) * 100}%`) : "Scroll";
  };
  const bind = (root = document) => {
    root.querySelectorAll("[data-diagram-controls]").forEach((controls) => {
      const id = controls.dataset.diagramControls;
      const canvas = id ? root.querySelector(`[data-diagram-id="${CSS.escape(id)}"]`) : null;
      if (!canvas || controls.dataset.diagramBound === "true") return;
      controls.dataset.diagramBound = "true";
      controls.addEventListener("click", (event) => {
        const button = event.target.closest("[data-diagram-zoom-control]");
        if (!button || !controls.contains(button)) return;
        const requested = button.dataset.diagramZoomControl;
        const applied = apply(canvas, requested);
        reflect(controls, requested, applied);
      });
    });
  };
  const enhance = (root = document) => {
    bind(root);
    root.querySelectorAll('[data-diagram-canvas][data-diagram-contract="1"]')
      .forEach((canvas) => {
        try {
          apply(canvas, canvas.dataset.diagramZoom || "fit");
        } catch (_error) {
          reset(canvas);
        }
      });
  };
  globalThis.VibeDiagramViewport = Object.freeze({ apply, bind, enhance, reset });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => enhance(), { once: true });
  } else {
    enhance();
  }
})();

(() => {
  "use strict";
  const reset = (root = document) => {
    root.querySelectorAll("[data-diagram-detail][data-runtime-open]").forEach((detail) => {
      detail.hidden = false;
      detail.removeAttribute("data-runtime-open");
    });
  };
  const open = (root, id) => {
    const detail = root.querySelector(`[data-diagram-detail="${CSS.escape(id)}"]`);
    if (!detail) return false;
    detail.hidden = false;
    detail.setAttribute("data-runtime-open", "true");
    detail.focus({ preventScroll: false });
    return true;
  };
  globalThis.VibeDiagramDisclosure = Object.freeze({ open, reset });
})();

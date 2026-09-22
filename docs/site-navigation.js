(() => {
  const media = window.matchMedia("(max-width: 720px)");
  const navigation = document.querySelector('nav[aria-label="Primary"]');
  const toggle = navigation?.querySelector("[data-navigation-toggle]");
  const links = navigation?.querySelector("#primary-navigation-links");

  if (!navigation || !toggle || !links) {
    return;
  }

  const setOpen = (open) => {
    toggle.setAttribute("aria-expanded", String(open));
    links.hidden = media.matches && !open;
  };

  const syncLayout = () => {
    document.documentElement.classList.toggle("navigation-ready", media.matches);
    setOpen(!media.matches);
  };

  toggle.addEventListener("click", () => {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && media.matches && !links.hidden) {
      setOpen(false);
      toggle.focus();
    }
  });

  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", syncLayout);
  } else {
    media.addListener(syncLayout);
  }
  syncLayout();
})();

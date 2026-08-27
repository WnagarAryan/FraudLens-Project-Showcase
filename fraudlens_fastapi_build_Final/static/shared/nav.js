(function () {
  const PAGES = [
    { href: "/", label: "Home" },
    { href: "/analyze.html", label: "Analyze" },
    { href: "/how-it-works.html", label: "How it works" },
    { href: "/reports.html", label: "Reports" },
    { href: "/about.html", label: "About" },
  ];
  
  const savedTheme = localStorage.getItem("fraudlens-theme");
  if (savedTheme) {
    document.documentElement.setAttribute("data-theme", savedTheme);
  }

  function currentPath() {
    const path = window.location.pathname;
    return path === "/index.html" ? "/" : path;
  }

  function renderNav() {
    const path = currentPath();
    const nav = document.createElement("nav");
    nav.className = "site-nav";

    const brand = document.createElement("a");
    brand.href = "/";
    brand.className = "nav-brand";
    brand.textContent = "FRAUDLENS";
    nav.appendChild(brand);

    const links = document.createElement("div");
    links.className = "nav-links";

    PAGES.forEach((p) => {
      const a = document.createElement("a");
      a.href = p.href;
      a.textContent = p.label;
      if (p.href === path) a.setAttribute("aria-current", "page");
      links.appendChild(a);
    });

    const toggle = document.createElement("button");
    toggle.id = "theme-toggle";
    toggle.className = "theme-dot";
    toggle.setAttribute("aria-label", "Toggle dark mode");
    toggle.addEventListener("click", () => {
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const next = isDark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("fraudlens-theme", next);
    });
    links.appendChild(toggle);

    nav.appendChild(links);
    document.body.prepend(nav);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderNav);
  } else {
    renderNav();
  }
})();

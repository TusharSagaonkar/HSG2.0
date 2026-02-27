/* Project specific Javascript goes here. */
const stopToggleEvent = (event) => {
  event.preventDefault();
  event.stopPropagation();
  if (typeof event.stopImmediatePropagation === "function") {
    event.stopImmediatePropagation();
  }
};

const initLayoutToggles = () => {
  const wrapper = document.querySelector(".wrapper");
  if (!wrapper) {
    return;
  }

  const htmlElement = document.documentElement;
  const sidebarToggles = Array.from(document.querySelectorAll(".toggle-sidebar"));
  const sidenavToggles = Array.from(document.querySelectorAll(".sidenav-toggler"));
  const topbarToggles = Array.from(document.querySelectorAll(".topbar-toggler"));
  const submenuLinks = Array.from(
    document.querySelectorAll(".submenu-link[data-submenu-target]"),
  );
  const submenuToggleZoneWidth = 44;

  const getSubmenuPanel = (submenuLink) => {
    const targetId = submenuLink.getAttribute("data-submenu-target");
    return targetId ? document.getElementById(targetId) : null;
  };

  const collapseAllSubmenus = (keepTargetId = null) => {
    submenuLinks.forEach((submenuLink) => {
      const panel = getSubmenuPanel(submenuLink);
      const targetId = submenuLink.getAttribute("data-submenu-target");
      const shouldStayOpen = keepTargetId && targetId === keepTargetId;

      if (panel) {
        panel.classList.toggle("show", shouldStayOpen);
      }

      submenuLink.setAttribute("aria-expanded", shouldStayOpen ? "true" : "false");
      const navItem = submenuLink.closest(".nav-item");
      if (navItem) {
        navItem.classList.toggle("submenu", shouldStayOpen);
      }
    });
  };

  const toggleSubmenuByLink = (submenuLink) => {
    if (!submenuLink) {
      return;
    }

    const panel = getSubmenuPanel(submenuLink);
    const targetId = submenuLink.getAttribute("data-submenu-target");
    if (!panel || !targetId) {
      return;
    }

    const shouldOpen = !panel.classList.contains("show");
    collapseAllSubmenus(shouldOpen ? targetId : null);
  };

  const isCaretZoneClick = (event, submenuLink) => {
    if (!submenuLink) {
      return false;
    }

    if (event.target.closest(".submenu-caret")) {
      return true;
    }

    const linkRect = submenuLink.getBoundingClientRect();
    return event.clientX >= linkRect.right - submenuToggleZoneWidth;
  };

  const syncSidebarToggleState = () => {
    const isMinimized = wrapper.classList.contains("sidebar_minimize");
    sidebarToggles.forEach((button) => {
      button.classList.toggle("toggled", isMinimized);
      button.setAttribute("aria-expanded", String(!isMinimized));

      const icon = button.querySelector("i");
      if (icon) {
        icon.className = isMinimized ? "gg-more-vertical-alt" : "gg-menu-right";
      }
    });
  };

  const syncSidenavToggleState = () => {
    const isOpen = htmlElement.classList.contains("nav_open");
    sidenavToggles.forEach((button) => {
      button.classList.toggle("toggled", isOpen);
      button.setAttribute("aria-expanded", String(isOpen));
    });
  };

  const syncTopbarToggleState = () => {
    const isOpen = htmlElement.classList.contains("topbar_open");
    topbarToggles.forEach((button) => {
      button.classList.toggle("toggled", isOpen);
      button.setAttribute("aria-expanded", String(isOpen));
    });
  };

  syncSidebarToggleState();
  syncSidenavToggleState();
  syncTopbarToggleState();
  collapseAllSubmenus();

  document.addEventListener(
    "click",
    (event) => {
      const submenuLink = event.target.closest(".submenu-link");
      if (submenuLink && isCaretZoneClick(event, submenuLink)) {
        stopToggleEvent(event);
        toggleSubmenuByLink(submenuLink);
        return;
      }

      if (submenuLink) {
        collapseAllSubmenus();
      }

      const submenuChildLink = event.target.closest(".nav-collapse a");
      if (submenuChildLink) {
        collapseAllSubmenus();
      }

      const sidebarToggle = event.target.closest(".toggle-sidebar");
      if (sidebarToggle) {
        stopToggleEvent(event);
        wrapper.classList.toggle("sidebar_minimize");
        syncSidebarToggleState();
        window.dispatchEvent(new Event("resize"));
        return;
      }

      const sidenavToggle = event.target.closest(".sidenav-toggler");
      if (sidenavToggle) {
        stopToggleEvent(event);
        htmlElement.classList.toggle("nav_open");
        syncSidenavToggleState();
        return;
      }

      const topbarToggle = event.target.closest(".topbar-toggler");
      if (topbarToggle) {
        stopToggleEvent(event);
        htmlElement.classList.toggle("topbar_open");
        syncTopbarToggleState();
      }
    },
    true,
  );
};

const initVoucherDetailModal = () => {
  const modalElement = document.getElementById("voucherDetailModal");

  if (!modalElement || !window.bootstrap) {
    return;
  }

  const titleElement = modalElement.querySelector("[data-voucher-detail-title]");
  const bodyElement = modalElement.querySelector("[data-voucher-detail-body]");
  const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);

  document.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-voucher-detail-url]");
    if (!trigger) {
      return;
    }

    event.preventDefault();

    const url = trigger.getAttribute("data-voucher-detail-url");
    if (!url) {
      return;
    }

    const label = trigger.getAttribute("data-voucher-label");
    if (titleElement && label) {
      titleElement.textContent = label;
    }
    if (bodyElement) {
      bodyElement.innerHTML = "<p class=\"text-muted mb-0\">Loading...</p>";
    }

    modal.show();

    try {
      const response = await fetch(url, {
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
      });

      if (!response.ok) {
        throw new Error("Failed to load voucher details");
      }

      if (bodyElement) {
        bodyElement.innerHTML = await response.text();
      }
    } catch (error) {
      if (bodyElement) {
        bodyElement.innerHTML =
          "<p class=\"text-danger mb-0\">Unable to load voucher details.</p>";
      }
    }
  });
};

const initAutoReloadSocietyForms = () => {
  const forms = Array.from(
    document.querySelectorAll("form[data-auto-reload-society=\"1\"]"),
  );
  if (!forms.length) {
    return;
  }

  forms.forEach((form) => {
    const societyField = form.querySelector("#id_society");
    if (!societyField) {
      return;
    }

    societyField.addEventListener("change", () => {
      const next = new URL(window.location.href);
      if (societyField.value) {
        next.searchParams.set("society", societyField.value);
      } else {
        next.searchParams.delete("society");
      }
      window.location.href = next.toString();
    });
  });
};

window.addEventListener("DOMContentLoaded", () => {
  initLayoutToggles();
  initVoucherDetailModal();
  initAutoReloadSocietyForms();
});

/* Project specific Javascript goes here. */

/**
 * Handle society selection from top bar dropdown - update session and navigate to society dashboard
 * @param {HTMLSelectElement} selectElement - The society selector dropdown
 */
const handleSocietySelection = (selectElement) => {
  // Keep user on the current page while only updating session selection.
  selectElement.form.submit();
};

const initShortcutHelpTrigger = () => {
  const triggers = Array.from(document.querySelectorAll("[data-shortcut-help-trigger]"));
  if (!triggers.length) {
    return;
  }

  triggers.forEach((trigger) => {
    trigger.addEventListener("click", () => {
      if (window.ShortcutEngine && typeof window.ShortcutEngine.showHelp === "function") {
        window.ShortcutEngine.showHelp();
      }
    });
  });
};

const initAccountTreeModal = () => {
  const modalElement = document.getElementById("accountFormModal");

  if (!modalElement || !window.bootstrap) {
    return;
  }

  const titleElement = modalElement.querySelector("[data-account-form-title]");
  const subtitleElement = modalElement.querySelector("[data-account-form-subtitle]");
  const bodyElement = modalElement.querySelector("[data-account-form-body]");
  const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);

  const setPlaceholder = () => {
    if (!bodyElement) {
      return;
    }

    bodyElement.innerHTML = `
      <div class="text-center py-4">
        <div class="spinner-border text-primary" role="status" aria-hidden="true"></div>
        <p class="text-muted mt-3 mb-0">Loading account form...</p>
      </div>
    `;
  };

  const focusFirstField = () => {
    const firstField = bodyElement?.querySelector("input:not([type=hidden]), select, textarea");
    if (firstField) {
      firstField.focus();
    }
  };

  const loadForm = async (url, trigger) => {
    if (titleElement) {
      titleElement.textContent = trigger?.getAttribute("data-account-form-title") || "Account";
    }
    if (subtitleElement) {
      const parentPath = trigger?.getAttribute("data-account-form-parent");
      subtitleElement.textContent = parentPath
        ? `Parent branch: ${parentPath}`
        : "Use this form to create or update the selected account.";
    }

    setPlaceholder();
    modal.show();

    try {
      const response = await fetch(url, {
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "text/html",
        },
      });

      if (!response.ok) {
        throw new Error("Failed to load account form");
      }

      if (bodyElement) {
        bodyElement.innerHTML = await response.text();
        focusFirstField();
      }
    } catch (error) {
      if (bodyElement) {
        bodyElement.innerHTML = `
          <div class="alert alert-danger mb-0" role="alert">
            Unable to load the account form. Please try again.
          </div>
        `;
      }
    }
  };

  const submitForm = async (form) => {
    if (!bodyElement) {
      return;
    }

    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        Accept: "application/json, text/html",
      },
    });

    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
      const payload = await response.json();
      if (response.ok && payload.success) {
        modal.hide();
        window.location.href = payload.redirect_url || window.location.href;
        return;
      }

      bodyElement.innerHTML = `
        <div class="alert alert-danger mb-3" role="alert">
          ${payload.message || "Unable to save the account."}
        </div>
      `;
      return;
    }

    bodyElement.innerHTML = await response.text();
    focusFirstField();
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-account-form-url]");
    if (!trigger) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    const url = trigger.getAttribute("data-account-form-url");
    if (!url) {
      return;
    }

    loadForm(url, trigger);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-account-form]");
    if (!form) {
      return;
    }

    event.preventDefault();
    submitForm(form).catch(() => {
      if (bodyElement) {
        bodyElement.innerHTML = `
          <div class="alert alert-danger mb-0" role="alert">
            The account could not be saved right now.
          </div>
        `;
      }
    });
  });

  modalElement.addEventListener("hidden.bs.modal", () => {
    if (bodyElement) {
      bodyElement.innerHTML = "";
    }
    if (titleElement) {
      titleElement.textContent = "Account";
    }
    if (subtitleElement) {
      subtitleElement.textContent = "Loading account form...";
    }
  });
};

const initPwa = () => {
  const installPrompt = document.querySelector("[data-pwa-install-prompt]");
  const installActions = Array.from(document.querySelectorAll("[data-pwa-install-action]"));
  const installDismiss = document.querySelector("[data-pwa-install-dismiss]");
  let deferredPrompt = null;

  const setInstallActionVisibility = (visible) => {
    installActions.forEach((action) => {
      action.classList.toggle("d-none", !visible);
    });
  };

  const isStandalone =
    window.matchMedia?.("(display-mode: standalone)")?.matches ||
    window.navigator.standalone === true;

  if (isStandalone && installPrompt) {
    installPrompt.remove();
    setInstallActionVisibility(false);
    return;
  }

  const hideInstallPrompt = (persist = false) => {
    if (installPrompt) {
      installPrompt.classList.add("d-none");
    }
    setInstallActionVisibility(false);
    deferredPrompt = null;
    if (persist) {
      sessionStorage.setItem("housing_accounting_pwa_install_dismissed", "true");
    }
  };

  if (sessionStorage.getItem("housing_accounting_pwa_install_dismissed") === "true") {
    hideInstallPrompt();
  }

  if (installDismiss) {
    installDismiss.addEventListener("click", () => {
      hideInstallPrompt(true);
    });
  }

  installActions.forEach((installAction) => {
    installAction.addEventListener("click", async () => {
      if (!deferredPrompt) {
        return;
      }

      deferredPrompt.prompt();
      await deferredPrompt.userChoice.catch(() => null);
      hideInstallPrompt();
    });
  });

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    if (!installPrompt) {
      return;
    }

    if (sessionStorage.getItem("housing_accounting_pwa_install_dismissed") === "true") {
      deferredPrompt = event;
      return;
    }

    deferredPrompt = event;
    installPrompt.classList.remove("d-none");
    setInstallActionVisibility(true);
  });

  window.addEventListener("appinstalled", () => {
    sessionStorage.removeItem("housing_accounting_pwa_install_dismissed");
    hideInstallPrompt();
  });

  if (!("serviceWorker" in navigator)) {
    return;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch((error) => {
      console.warn("Service worker registration failed:", error);
    });
  });
};

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
  const sidebarWrapper = document.querySelector(".sidebar-wrapper");
  const sidebarToggles = Array.from(document.querySelectorAll(".toggle-sidebar"));
  const sidenavToggles = Array.from(document.querySelectorAll(".sidenav-toggler"));
  const topbarToggles = Array.from(document.querySelectorAll(".topbar-toggler"));
  const submenuLinks = Array.from(
    document.querySelectorAll(".submenu-link[data-submenu-target]"),
  );
  const submenuToggleZoneWidth = 44;

  // localStorage keys for persisting state
  const SIDEBAR_STATE_KEY = "housing_accounting_sidebar_minimized";
  const OPEN_SUBMENU_KEY = "housing_accounting_open_submenu";
  const SIDEBAR_SCROLL_KEY = "housing_accounting_sidebar_scroll_top";

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
    if (keepTargetId) {
      localStorage.setItem(OPEN_SUBMENU_KEY, keepTargetId);
    } else {
      localStorage.removeItem(OPEN_SUBMENU_KEY);
    }
    window.setTimeout(() => {
      saveSidebarScrollState();
    }, 0);
  };

  const openSubmenuByTargetId = (targetId) => {
    if (!targetId) {
      return;
    }
    const submenuLink = submenuLinks.find(
      (link) => link.getAttribute("data-submenu-target") === targetId,
    );
    if (!submenuLink) {
      return;
    }
    const panel = getSubmenuPanel(submenuLink);
    if (!panel) {
      return;
    }
    collapseAllSubmenus(targetId);
    window.setTimeout(() => {
      ensureExpandedSubmenuVisible(submenuLink, panel);
    }, 220);
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
    if (shouldOpen) {
      window.setTimeout(() => {
        ensureExpandedSubmenuVisible(submenuLink, panel);
      }, 220);
    }
  };

  const getFirstSubmenuHref = (submenuLink) => {
    const panel = getSubmenuPanel(submenuLink);
    if (!panel) {
      return null;
    }
    const firstSubmenuLink = panel.querySelector(".nav-collapse a[href]");
    if (!firstSubmenuLink) {
      return null;
    }
    const href = firstSubmenuLink.getAttribute("href");
    return href && href.trim() ? href : null;
  };

  const ensureExpandedSubmenuVisible = (submenuLink, panel) => {
    if (!sidebarWrapper || !submenuLink || !panel || !panel.classList.contains("show")) {
      return;
    }

    const wrapperRect = sidebarWrapper.getBoundingClientRect();
    const linkRect = submenuLink.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const topPadding = 12;
    const bottomPadding = 12;

    if (linkRect.top < wrapperRect.top + topPadding) {
      sidebarWrapper.scrollTop -= (wrapperRect.top + topPadding - linkRect.top);
    }

    if (panelRect.bottom > wrapperRect.bottom - bottomPadding) {
      sidebarWrapper.scrollTop += (panelRect.bottom - (wrapperRect.bottom - bottomPadding));
    }
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

  // Load saved sidebar state from localStorage and apply it immediately
  const loadSavedSidebarState = () => {
    const savedState = localStorage.getItem(SIDEBAR_STATE_KEY);
    const isMinimized = htmlElement.getAttribute("data-sidebar-minimized") === "true" || savedState === "true";
    
    if (isMinimized) {
      wrapper.classList.add("sidebar_minimize");
    } else {
      wrapper.classList.remove("sidebar_minimize");
    }
  };

  // Save sidebar state to localStorage
  const saveSidebarState = () => {
    const isMinimized = wrapper.classList.contains("sidebar_minimize");
    localStorage.setItem(SIDEBAR_STATE_KEY, isMinimized);
  };

  const loadSidebarScrollState = () => {
    if (!sidebarWrapper) {
      return;
    }
    const savedScrollTop = Number(localStorage.getItem(SIDEBAR_SCROLL_KEY));
    if (Number.isFinite(savedScrollTop) && savedScrollTop >= 0) {
      sidebarWrapper.scrollTop = savedScrollTop;
    }
  };

  const saveSidebarScrollState = () => {
    if (!sidebarWrapper) {
      return;
    }
    localStorage.setItem(SIDEBAR_SCROLL_KEY, String(sidebarWrapper.scrollTop));
  };

  // Load saved state on initialization
  loadSavedSidebarState();

  syncSidebarToggleState();
  syncSidenavToggleState();
  syncTopbarToggleState();
  collapseAllSubmenus();

  // Keep the active section open after load.
  const savedOpenSubmenuId = localStorage.getItem(OPEN_SUBMENU_KEY);
  if (savedOpenSubmenuId) {
    openSubmenuByTargetId(savedOpenSubmenuId);
  }

  const activeSubmenuLink = submenuLinks.find((submenuLink) => {
    const navItem = submenuLink.closest(".nav-item");
    return navItem && navItem.classList.contains("active");
  });
  if (activeSubmenuLink && !savedOpenSubmenuId) {
    openSubmenuByTargetId(activeSubmenuLink.getAttribute("data-submenu-target"));
  }

  loadSidebarScrollState();
  if (sidebarWrapper) {
    sidebarWrapper.addEventListener("scroll", saveSidebarScrollState, { passive: true });
  }

  // Hover opens submenu in accordion mode (no auto-close on mouseleave).
  submenuLinks.forEach((submenuLink) => {
    const targetId = submenuLink.getAttribute("data-submenu-target");
    if (!targetId) {
      return;
    }
    submenuLink.addEventListener("mouseenter", () => {
      openSubmenuByTargetId(targetId);
    });
  });

  document.addEventListener(
    "click",
    (event) => {
      const submenuLink = event.target.closest(".submenu-link");
      if (submenuLink) {
        if (wrapper.classList.contains("sidebar_minimize")) {
          // In icon mode, clicking submenu row should behave like main menu click.
          const firstSubmenuHref = getFirstSubmenuHref(submenuLink);
          if (firstSubmenuHref) {
            stopToggleEvent(event);
            window.location.href = firstSubmenuHref;
            return;
          }
        }

        if (isCaretZoneClick(event, submenuLink)) {
          stopToggleEvent(event);
          if (wrapper.classList.contains("sidebar_minimize")) {
            wrapper.classList.remove("sidebar_minimize");
            saveSidebarState();
            syncSidebarToggleState();
          }
          toggleSubmenuByLink(submenuLink);
          return;
        }

        // Main row click should behave like the first submenu item.
        const firstSubmenuHref = getFirstSubmenuHref(submenuLink);
        if (firstSubmenuHref) {
          stopToggleEvent(event);
          window.location.href = firstSubmenuHref;
          return;
        }
      }

      const sidebarToggle = event.target.closest(".toggle-sidebar");
      if (sidebarToggle) {
        stopToggleEvent(event);
        wrapper.classList.toggle("sidebar_minimize");
        saveSidebarState();
        syncSidebarToggleState();
        saveSidebarScrollState();
        window.dispatchEvent(new Event("resize"));
        return;
      }

      // Auto-collapse mobile sidenav when a sidebar link is clicked.
      const sidebarLink = event.target.closest('.sidebar a[href]');
      const isMobile = window.matchMedia && window.matchMedia('(max-width: 991px)').matches;
      if (sidebarLink && isMobile && htmlElement.classList.contains('nav_open')) {
        // Allow navigation to proceed, but close the mobile nav UI.
        htmlElement.classList.remove('nav_open');
        syncSidenavToggleState();
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
        return;
      }

      // Keep accordion state stable; do not auto-close submenus on outside click.
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

const initAutoReloadUnitForms = () => {
  const forms = Array.from(
    document.querySelectorAll("form[data-auto-reload-unit=\"1\"]"),
  );
  if (!forms.length) {
    return;
  }

  forms.forEach((form) => {
    const societyField = form.querySelector("#id_society");
    const unitField = form.querySelector("#id_unit");
    const memberField = form.querySelector("#id_member");
    const memberLookupUrl = form.getAttribute("data-member-lookup-url");
    if (!unitField) {
      return;
    }

    const applyMemberOptions = (members) => {
      if (!memberField) {
        return;
      }
      const existing = memberField.value;
      memberField.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "---------";
      memberField.appendChild(placeholder);

      members.forEach((member) => {
        const option = document.createElement("option");
        option.value = String(member.id);
        option.textContent = member.full_name;
        if (String(member.id) === String(existing)) {
          option.selected = true;
        }
        memberField.appendChild(option);
      });
    };

    const loadMembersForUnit = async () => {
      if (!memberLookupUrl || !memberField) {
        return false;
      }
      const societyValue = societyField ? societyField.value : "";
      const unitValue = unitField.value;
      if (!societyValue || !unitValue) {
        applyMemberOptions([]);
        return true;
      }
      const lookupUrl = new URL(memberLookupUrl, window.location.origin);
      lookupUrl.searchParams.set("society", societyValue);
      lookupUrl.searchParams.set("unit", unitValue);
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 5000);
      try {
        memberField.disabled = true;
        const response = await fetch(lookupUrl.toString(), {
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("Unable to fetch members");
        }
        const payload = await response.json();
        applyMemberOptions(payload.members || []);
        return true;
      } catch (error) {
        applyMemberOptions([]);
        return false;
      } finally {
        window.clearTimeout(timer);
        memberField.disabled = false;
      }
    };

    unitField.addEventListener("change", async () => {
      await loadMembersForUnit();
    });

    if (unitField.value) {
      loadMembersForUnit();
    }
  });
};

const initUnitSearchForms = () => {
  const forms = Array.from(document.querySelectorAll("form[data-unit-search-url]"));
  if (!forms.length) {
    return;
  }

  forms.forEach((form) => {
    const searchField = form.querySelector("#id_unit_search");
    const hiddenUnitField = form.querySelector("#id_unit");
    const resultsContainer = form.querySelector("[data-unit-search-results]");
    const societyField = form.querySelector("#id_society");
    const searchUrl = form.getAttribute("data-unit-search-url");

    if (!searchField || !hiddenUnitField || !resultsContainer || !searchUrl) {
      return;
    }

    let debounceTimer = null;

    const clearResults = () => {
      resultsContainer.innerHTML = "";
      resultsContainer.classList.add("d-none");
    };

    const selectUnit = (unit) => {
      hiddenUnitField.value = String(unit.id);
      searchField.value = unit.label;
      clearResults();
    };

    const renderResults = (units) => {
      resultsContainer.innerHTML = "";
      if (!units.length) {
        clearResults();
        return;
      }
      units.forEach((unit) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        button.textContent = unit.label;
        button.addEventListener("click", () => selectUnit(unit));
        resultsContainer.appendChild(button);
      });
      resultsContainer.classList.remove("d-none");
    };

    const searchUnits = async () => {
      const q = searchField.value.trim();
      const societyId = societyField ? societyField.value : "";
      if (!societyId || !q) {
        clearResults();
        return;
      }

      const url = new URL(searchUrl, window.location.origin);
      url.searchParams.set("society_id", societyId);
      url.searchParams.set("q", q);

      try {
        const response = await fetch(url.toString(), {
          headers: {
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        const data = await response.json();
        renderResults(data.units || []);
      } catch (error) {
        clearResults();
      }
    };

    searchField.addEventListener("input", () => {
      hiddenUnitField.value = "";
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(searchUnits, 250);
    });

    searchField.addEventListener("focus", searchUnits);

    document.addEventListener("click", (event) => {
      if (!form.contains(event.target)) {
        clearResults();
      }
    });
  });
};

const initStructureHierarchyFilters = () => {
  const filterBar = document.querySelector("[data-structure-filters]");
  if (!filterBar) {
    return;
  }

  const searchField = filterBar.querySelector("[data-structure-search]");
  const structureTypeField = filterBar.querySelector("[data-structure-type-filter]");
  const unitStatusField = filterBar.querySelector("[data-unit-status-filter]");
  const occupancyField = filterBar.querySelector("[data-occupancy-filter]");
  const resetButton = filterBar.querySelector("[data-structure-filter-reset]");
  const summary = document.querySelector("[data-structure-filter-summary]");
  const structureNodes = Array.from(document.querySelectorAll("[data-structure-node]"));

  if (!searchField || !structureTypeField || !unitStatusField || !occupancyField) {
    return;
  }

  const applyFilters = () => {
    const searchTerm = searchField.value.trim().toLowerCase();
    const structureType = structureTypeField.value;
    const unitStatus = unitStatusField.value;
    const occupancy = occupancyField.value;

    const evaluateNode = (node) => {
      const accordion = node.querySelector(":scope > .structure-accordion");
      const collapseElement = accordion?.querySelector(":scope > [data-structure-collapse]");
      const childList = collapseElement?.querySelector(":scope > .structure-children");
      const unitGrid = collapseElement?.querySelector(":scope > [data-structure-unit-grid]");
      const childNodes = childList
        ? Array.from(childList.querySelectorAll(":scope > [data-structure-node]"))
        : [];
      const unitCards = unitGrid
        ? Array.from(unitGrid.querySelectorAll(":scope > [data-unit-card]"))
        : [];
      const ownName = (node.dataset.structureSearch || "").trim();
      const structureTypeMatch = !structureType || node.dataset.structureType === structureType;
      const structureSearchMatch = !searchTerm || ownName.includes(searchTerm);

      let visibleUnitCount = 0;
      unitCards.forEach((card) => {
        const unitSearch = card.dataset.unitSearch || "";
        const statusMatch = !unitStatus || card.dataset.unitActive === unitStatus;
        const occupancyMatch = !occupancy || card.dataset.unitOccupancy === occupancy;
        const searchMatch = !searchTerm || unitSearch.includes(searchTerm);
        const matches = statusMatch && occupancyMatch && searchMatch;
        card.style.display = matches ? "" : "none";
        if (matches) {
          visibleUnitCount += 1;
        }
      });

      let visibleChildCount = 0;
      childNodes.forEach((childNode) => {
        const childVisible = evaluateNode(childNode);
        childNode.style.display = childVisible ? "" : "none";
        if (childVisible) {
          visibleChildCount += 1;
        }
      });

      const shouldShow =
        (structureTypeMatch && structureSearchMatch) ||
        visibleUnitCount > 0 ||
        visibleChildCount > 0;

      if (collapseElement && window.bootstrap && window.bootstrap.Collapse) {
        const collapseInstance = window.bootstrap.Collapse.getOrCreateInstance(collapseElement, {
          toggle: false,
        });
        if (shouldShow && (searchTerm || unitStatus || occupancy || structureType)) {
          collapseInstance.show();
        }
      }

      return shouldShow;
    };

    let visibleStructures = 0;
    let visibleUnits = 0;

    structureNodes
      .filter((node) => !node.parentElement.closest("[data-structure-node]"))
      .forEach((node) => {
        const visible = evaluateNode(node);
        node.style.display = visible ? "" : "none";
      });

    structureNodes.forEach((node) => {
      if (node.style.display !== "none") {
        visibleStructures += 1;
      }
    });

    document.querySelectorAll("[data-unit-card]").forEach((card) => {
      if (card.style.display !== "none") {
        visibleUnits += 1;
      }
    });

    if (summary) {
      if (!searchTerm && !structureType && !unitStatus && !occupancy) {
        summary.textContent = "Showing full structure hierarchy.";
      } else {
        summary.textContent = `Showing ${visibleStructures} matching structures and ${visibleUnits} matching units.`;
      }
    }
  };

  [searchField, structureTypeField, unitStatusField, occupancyField].forEach((field) => {
    field.addEventListener("input", applyFilters);
    field.addEventListener("change", applyFilters);
  });

  if (resetButton) {
    resetButton.addEventListener("click", () => {
      searchField.value = "";
      structureTypeField.value = "";
      unitStatusField.value = "";
      occupancyField.value = "";
      applyFilters();
    });
  }

  applyFilters();
};

const initWorkspaceContent = () => {
  initAutoReloadSocietyForms();
  initAutoReloadUnitForms();
  initUnitSearchForms();
  initStructureHierarchyFilters();
};

const initPersistentWorkspace = () => {
  const workspace = document.getElementById("workspace");
  const dashboardHeading = document.querySelector(".dashboard-heading h3");
  const dashboardSubtitle = document.querySelector(".page-inner > .d-flex h6");

  if (!workspace) {
    return;
  }

  const normalizePath = (href) => {
    try {
      const url = new URL(href, window.location.origin);
      return url.pathname.replace(/\/+$/, "") || "/";
    } catch (error) {
      return href.replace(/\/+$/, "") || "/";
    }
  };

  const isWorkspaceEligibleLink = (link) => {
    const rawHref = link.getAttribute("href") || "";
    if (!rawHref || rawHref.startsWith("#")) {
      return false;
    }
    if (link.hasAttribute("download") || link.getAttribute("target") && link.getAttribute("target") !== "_self") {
      return false;
    }
    if (link.hasAttribute("data-bs-toggle") || link.closest("[data-no-workspace]")) {
      return false;
    }
    if (link.hasAttribute("hx-post") || link.hasAttribute("hx-delete") || link.hasAttribute("hx-put") || link.hasAttribute("hx-patch")) {
      return false;
    }

    try {
      const url = new URL(rawHref, window.location.origin);
      return url.origin === window.location.origin && ["http:", "https:"].includes(url.protocol);
    } catch (error) {
      return false;
    }
  };

  const activateCurrentLink = () => {
    const currentPath = normalizePath(window.location.href);
    document.querySelectorAll(".sidebar a[href]").forEach((link) => {
      const isActive = normalizePath(link.href) === currentPath;
      link.classList.toggle("active", isActive);
      link.setAttribute("aria-current", isActive ? "page" : "false");

      const navItem = link.closest(".nav-item");
      if (navItem) {
        navItem.classList.toggle("active", isActive);
      }
    });
  };

  const loadPageAssets = (parsedDocument) => {
    parsedDocument.querySelectorAll('link[rel="stylesheet"][href]').forEach((asset) => {
      if (!document.querySelector(`link[rel="stylesheet"][href="${asset.href}"]`)) {
        document.head.appendChild(asset.cloneNode(true));
      }
    });

    parsedDocument.querySelectorAll("script[src]").forEach((asset) => {
      if (document.querySelector(`script[src="${asset.src}"]`)) {
        return;
      }
      const script = document.createElement("script");
      script.src = asset.src;
      script.defer = true;
      document.body.appendChild(script);
    });
  };

  const syncPersistentChrome = (responseText) => {
    if (!responseText) {
      return;
    }

    const parsedDocument = new DOMParser().parseFromString(responseText, "text/html");
    const nextTitle = parsedDocument.querySelector("title");
    const nextHeading = parsedDocument.querySelector(".dashboard-heading h3");
    const nextSubtitle = parsedDocument.querySelector(".page-inner > .d-flex h6");

    if (nextTitle) {
      document.title = nextTitle.textContent.trim();
    }
    if (dashboardHeading && nextHeading) {
      dashboardHeading.textContent = nextHeading.textContent.trim();
    }
    if (dashboardSubtitle && nextSubtitle) {
      dashboardSubtitle.textContent = nextSubtitle.textContent.trim();
    }
    loadPageAssets(parsedDocument);
  };

  const enhanceWorkspaceLinks = (root = document) => {
    root.querySelectorAll("a[href]").forEach((link) => {
      if (!isWorkspaceEligibleLink(link)) {
        return;
      }
      link.setAttribute("hx-get", link.getAttribute("href"));
      link.setAttribute("hx-target", "#workspace");
      link.setAttribute("hx-select", "#workspace");
      link.setAttribute("hx-swap", "innerHTML");
      link.setAttribute("hx-push-url", "true");
      link.setAttribute("hx-indicator", "#workspace-loading");
      if (window.htmx) {
        window.htmx.process(link);
      }
    });
  };

  enhanceWorkspaceLinks();
  activateCurrentLink();

  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail && event.detail.target === workspace) {
      syncPersistentChrome(event.detail.xhr?.responseText || "");
      activateCurrentLink();
      initWorkspaceContent();
      enhanceWorkspaceLinks(workspace);
      workspace.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "auto" });
    }
  });

  document.body.addEventListener("htmx:historyRestore", () => {
    activateCurrentLink();
    initWorkspaceContent();
    enhanceWorkspaceLinks(workspace);
  });
  window.addEventListener("popstate", activateCurrentLink);
};

window.addEventListener("DOMContentLoaded", () => {
  initLayoutToggles();
  initVoucherDetailModal();
  initAccountTreeModal();
  initWorkspaceContent();
  initShortcutHelpTrigger();
  initPwa();
  initPersistentWorkspace();
});

/* Project specific Javascript goes here. */

/**
 * Handle society selection from top bar dropdown - update session and navigate to society dashboard
 * @param {HTMLSelectElement} selectElement - The society selector dropdown
 */
const handleSocietySelection = (selectElement) => {
  // Keep user on the current page while only updating session selection.
  selectElement.form.submit();
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

const initStructureHierarchyFilters = () => {
  const filterBar = document.querySelector("[data-structure-filters]");
  if (!filterBar || !window.bootstrap) {
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

      if (collapseElement) {
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

window.addEventListener("DOMContentLoaded", () => {
  initLayoutToggles();
  initVoucherDetailModal();
  initAutoReloadSocietyForms();
  initAutoReloadUnitForms();
  initStructureHierarchyFilters();
});

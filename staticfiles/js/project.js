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

  document.addEventListener(
    "click",
    (event) => {
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

window.addEventListener("DOMContentLoaded", () => {
  initLayoutToggles();
  initVoucherDetailModal();
  initAutoReloadSocietyForms();
  initAutoReloadUnitForms();
});

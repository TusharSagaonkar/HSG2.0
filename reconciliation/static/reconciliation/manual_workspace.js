/**
 * Manual Workspace — statement tape entry modal wiring.
 *
 * Handles:
 *   - Clicking the "Entry" button on a statement tape row
 *   - Prefilling the modal form from row data-* attributes
 *   - Opening / closing the Bootstrap modal
 *   - Resetting form state on modal close
 *   - Closing modal after successful htmx form submission
 *   - Keyboard navigation (arrow keys, Enter, Escape)
 *   - Error handling for failed requests
 */

(function () {
  "use strict";

  var MODAL_ID = "manual-entry-modal";
  var FORM_ID = "manual-row-form";
  var ROWS_BODY_ID = "manual-workspace-rows-body";

  /* ── Public API (exposed for inline onclick handlers) ─────────────── */

  /**
   * Open the manual-entry modal for a given trigger button.
   * Called from inline onclick to avoid event-propagation conflicts
   * with htmx hx-get on the parent <tr>.
   */
  window.manualWorkspaceOpenEntry = function (btnEl) {
    var row = btnEl.closest("tr.manual-grid-row");
    if (!row) return;

    // Update modal title based on whether we're editing or creating
    var isEdit = row.dataset.txId && row.dataset.txId !== "";
    var modalLabel = document.getElementById("manual-entry-modal-label");
    if (modalLabel) {
      modalLabel.textContent = isEdit ? "Edit Bank Entry" : "New Bank Entry";
    }

    prefillFromRow(row);
    openModal();
  };

  /* ── DOM helpers ──────────────────────────────────────────────────── */

  function getModalEl() {
    return document.getElementById(MODAL_ID);
  }

  function getFormEl() {
    return document.getElementById(FORM_ID);
  }

  function getRowsBodyEl() {
    return document.getElementById(ROWS_BODY_ID);
  }

  /**
   * Find a form input by its data-grid-input attribute or fall back to
   * the Django auto-generated id (id_<field_name>).
   */
  function findInput(form, gridKey, fieldName) {
    return form.querySelector('[data-grid-input="' + gridKey + '"]')
      || form.querySelector("#id_" + fieldName);
  }

  /* ── Prefill logic ────────────────────────────────────────────────── */

  /**
   * Prefill the modal form from a statement tape row's data attributes.
   *
   * Row <tr> attributes used:
   *   data-tx-id            → selected_tx_id (hidden)
   *   data-transaction-date → transaction_date
   *   data-narration        → narration
   *   data-reference-no     → reference_no
   *   data-dr-cr            → determines debit vs credit
   *   data-amount           → debit or credit value
   *   data-balance          → balance
   */
  function prefillFromRow(row) {
    var form = getFormEl();
    if (!form) return;

    var dateInput      = findInput(form, "date",         "transaction_date");
    var narrationInput = findInput(form, "narration",     "narration");
    var refInput       = findInput(form, "reference_no", "reference_no");
    var debitInput     = findInput(form, "debit",        "debit");
    var creditInput    = findInput(form, "credit",       "credit");
    var balanceInput   = findInput(form, "balance",      "balance");
    var txIdInput      = form.querySelector('[name="selected_tx_id"]');

    var txId            = row.dataset.txId            || "";
    var transactionDate = row.dataset.transactionDate || "";
    var narration       = row.dataset.narration       || "";
    var referenceNo     = row.dataset.referenceNo     || "";
    var drCr            = row.dataset.drCr            || "";
    var amount          = row.dataset.amount          || "";
    var balance         = row.dataset.balance         || "";

    if (dateInput)      dateInput.value      = transactionDate;
    if (narrationInput) narrationInput.value = narration;
    if (refInput)       refInput.value       = referenceNo;
    if (balanceInput)   balanceInput.value   = balance || "";
    if (txIdInput)      txIdInput.value      = txId;

    // Set debit or credit based on dr_cr direction
    if (drCr === "DEBIT") {
      if (debitInput)  debitInput.value  = amount;
      if (creditInput) creditInput.value = "";
    } else {
      if (creditInput) creditInput.value = amount;
      if (debitInput)  debitInput.value  = "";
    }

    // Clear any leftover validation error styling
    clearValidationErrors(form);
    clearFormErrors();
  }

  /**
   * Remove Bootstrap validation error classes and feedback messages
   * left over from a previous submission attempt.
   */
  function clearValidationErrors(form) {
    form.querySelectorAll(".is-invalid").forEach(function (el) {
      el.classList.remove("is-invalid");
    });
    form.querySelectorAll(".invalid-feedback").forEach(function (el) {
      el.remove();
    });
  }

  /**
   * Clear custom error messages from the form error container.
   */
  function clearFormErrors() {
    var errorContainer = document.getElementById("manual-row-form-errors");
    if (errorContainer) {
      errorContainer.innerHTML = "";
    }
  }

  /**
   * Display an error message in the form.
   */
  function showFormError(message) {
    var errorContainer = document.getElementById("manual-row-form-errors");
    if (!errorContainer) return;

    errorContainer.innerHTML = `
      <div class="alert alert-danger alert-dismissible fade show mt-2" role="alert">
        <strong>Error:</strong> ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
      </div>
    `;
  }

  /* ── Modal open / close ───────────────────────────────────────────── */

  function openModal() {
    var modalEl = getModalEl();
    if (!modalEl) return;
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();

    // Focus on the first input after modal opens
    setTimeout(function () {
      var firstInput = modalEl.querySelector("input:not([type='hidden'])");
      if (firstInput) firstInput.focus();
    }, 150);
  }

  function closeModal() {
    var modalEl = getModalEl();
    if (!modalEl) return;
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.hide();
  }

  /**
   * Reset the modal form to a clean state so the next open starts fresh.
   */
  function resetForm() {
    var form = getFormEl();
    if (!form) return;
    form.reset();
    clearValidationErrors(form);
    clearFormErrors();
  }

  /* ── Keyboard navigation ──────────────────────────────────────────── */

  /**
   * Navigate between rows using arrow keys.
   * Enter opens the selected row in the modal.
   */
  function handleKeyboardNavigation(e) {
    // Don't handle keys when modal is open
    var modalEl = getModalEl();
    if (modalEl && modalEl.classList.contains("show")) return;

    // Don't handle keys when typing in an input
    var activeTag = document.activeElement.tagName.toLowerCase();
    if (activeTag === "input" || activeTag === "textarea" || activeTag === "select") return;

    var rows = document.querySelectorAll(".manual-grid-row");
    if (!rows.length) return;

    var active = document.querySelector(".manual-grid-row.table-active");
    var idx = active ? Array.from(rows).indexOf(active) : -1;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (idx < rows.length - 1) {
          rows[idx + 1].click();
          rows[idx + 1].focus();
          rows[idx + 1].scrollIntoView({ block: "nearest" });
        }
        break;

      case "ArrowUp":
        e.preventDefault();
        if (idx > 0) {
          rows[idx - 1].click();
          rows[idx - 1].focus();
          rows[idx - 1].scrollIntoView({ block: "nearest" });
        }
        break;

      case "Enter":
        e.preventDefault();
        if (active) {
          var btn = active.querySelector("[data-open-manual-entry]");
          if (btn) btn.click();
        }
        break;

      case "Escape":
        // Close modal if open
        if (modalEl && modalEl.classList.contains("show")) {
          closeModal();
        }
        break;

      case "n":
      case "N":
        // 'N' key opens a new entry modal (if not in input)
        if (!active) {
          var newBtn = document.querySelector("[data-open-manual-entry]");
          if (newBtn) newBtn.click();
        }
        break;
    }
  }

  /* ── Row highlighting ─────────────────────────────────────────────── */

  /**
   * Add a highlight class to a newly added row for visual feedback.
   */
  function highlightNewRow(row) {
    if (!row) return;
    row.classList.add("manual-grid-row-new");
    setTimeout(function () {
      row.classList.remove("manual-grid-row-new");
    }, 1500);
  }

  /* ── Event listeners ──────────────────────────────────────────────── */

  // Click on "Entry" button → prefill + open modal
  // NOTE: Buttons inside <tr hx-get> rows use inline onclick calling
  // window.manualWorkspaceOpenEntry(this) with event.stopPropagation()
  // to prevent htmx from firing. This document-level listener is kept
  // as a fallback for buttons that do NOT have the inline handler.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-open-manual-entry]");
    if (!btn) return;

    // If the button already has an inline onclick handler, skip —
    // the inline handler already handled prefill + openModal.
    if (btn.hasAttribute("onclick")) return;

    e.stopPropagation();
    e.preventDefault();

    var row = btn.closest("tr.manual-grid-row");
    if (!row) return;

    prefillFromRow(row);
    openModal();
  });

  // Keyboard navigation
  document.addEventListener("keydown", handleKeyboardNavigation);

  // Reset form when modal is hidden (user cancelled or closed)
  var modalEl = getModalEl();
  if (modalEl) {
    modalEl.addEventListener("hidden.bs.modal", function () {
      resetForm();
    });
  }

  // Handle htmx form submission
  document.addEventListener("htmx:afterRequest", function (e) {
    var form = getFormEl();
    if (!form) return;

    // Only react to requests originating from our modal form
    if (e.detail.elt && e.detail.elt.id === FORM_ID) {
      if (e.detail.successful) {
        closeModal();

        // Highlight the newly added row
        var rowsBody = getRowsBodyEl();
        if (rowsBody) {
          var lastRow = rowsBody.querySelector("tr.manual-grid-row:last-child");
          if (lastRow) {
            highlightNewRow(lastRow);
            lastRow.scrollIntoView({ block: "nearest" });
          }
        }
      } else {
        // Show error message
        var errorMessage = "Failed to save row. Please check the form and try again.";
        if (e.detail.xhr && e.detail.xhr.responseText) {
          try {
            var response = JSON.parse(e.detail.xhr.responseText);
            if (response.error) {
              errorMessage = response.error;
            }
          } catch (ex) {
            // Not JSON, use status text
            if (e.detail.xhr.statusText) {
              errorMessage = "Server error: " + e.detail.xhr.statusText;
            }
          }
        }
        showFormError(errorMessage);
      }
    }
  });

  // Handle htmx before request (show loading state)
  document.addEventListener("htmx:beforeRequest", function (e) {
    var form = getFormEl();
    if (!form) return;

    if (e.detail.elt && e.detail.elt.id === FORM_ID) {
      clearFormErrors();
    }
  });

})();

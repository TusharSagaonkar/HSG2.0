(function () {
  const isTypingContext = (element) => {
    if (!element) {
      return false;
    }
    const tagName = element.tagName ? element.tagName.toLowerCase() : "";
    return tagName === "input" || tagName === "textarea" || tagName === "select";
  };

  const getActiveRow = () => document.querySelector("#manual-workspace-rows-body tr.table-active");

  const getRows = () =>
    Array.from(document.querySelectorAll("#manual-workspace-rows-body tr[data-tx-id]"));

  const getGridFields = () =>
    Array.from(document.querySelectorAll("#manual-row-form [data-grid-input]"))
      .filter((field) => field.type !== "hidden" && !field.disabled);

  const getEntryModal = () => document.getElementById("manual-entry-modal");

  const getSelectedRowData = (row) => {
    if (!row) {
      return null;
    }
    return {
      date: row.dataset.transactionDate || "",
      narration: row.dataset.narration || "",
      referenceNo: row.dataset.referenceNo || "",
      drCr: row.dataset.drCr || "",
      amount: row.dataset.amount || "",
      balance: row.dataset.balance || "",
      txId: row.dataset.txId || "",
    };
  };

  const getTodayIsoDate = () => new Date().toISOString().slice(0, 10);

  const setFieldValue = (selector, value) => {
    const field = document.querySelector(selector);
    if (!field) {
      return;
    }
    field.value = value ?? "";
  };

  const prefillEntryForm = (row) => {
    const data = getSelectedRowData(row);
    if (!data) {
      setFieldValue("#id_transaction_date", getTodayIsoDate());
      setFieldValue("#id_narration", "");
      setFieldValue("#id_reference_no", "");
      setFieldValue("#id_balance", "");
      setFieldValue("#id_selected_tx_id", "");
      const debitField = document.querySelector("#id_debit");
      const creditField = document.querySelector("#id_credit");
      if (debitField && creditField) {
        debitField.value = "";
        creditField.value = "";
      }
      return;
    }

    setFieldValue("#id_transaction_date", data.date);
    setFieldValue("#id_narration", data.narration);
    setFieldValue("#id_reference_no", data.referenceNo);
    setFieldValue("#id_balance", data.balance);
    setFieldValue("#id_selected_tx_id", data.txId);

    const debitField = document.querySelector("#id_debit");
    const creditField = document.querySelector("#id_credit");
    if (debitField && creditField) {
      if (data.drCr === "DEBIT") {
        debitField.value = data.amount;
        creditField.value = "";
      } else if (data.drCr === "CREDIT") {
        creditField.value = data.amount;
        debitField.value = "";
      } else {
        debitField.value = "";
        creditField.value = "";
      }
    }
  };

  const openEntryModal = (row) => {
    const modalEl = getEntryModal();
    prefillEntryForm(row || getActiveRow() || getRows()[0]);
    if (!modalEl) {
      return;
    }
    if (window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
      return;
    }
    modalEl.classList.add("show");
    modalEl.style.display = "block";
  };

  const setActiveRow = (row) => {
    if (!row) {
      return;
    }
    document.querySelectorAll("#manual-workspace-rows-body tr.table-active").forEach((active) => {
      active.classList.remove("table-active");
    });
    row.classList.add("table-active");
    const selectedTxInput = document.querySelector("#id_selected_tx_id");
    if (selectedTxInput) {
      selectedTxInput.value = row.getAttribute("data-tx-id") || "";
    }
  };

  const focusSearch = () => {
    const searchField = document.querySelector("#id_search_voucher");
    if (searchField) {
      searchField.focus();
      searchField.select?.();
    }
  };

  const triggerShortcutAction = (actionName) => {
    const button = document.querySelector(`[data-shortcut-action="${actionName}"]`);
    if (button) {
      button.click();
    }
  };

  const moveRowSelection = (direction) => {
    const rows = getRows();
    if (!rows.length) {
      return;
    }
    const activeRow = getActiveRow() || rows[0];
    const index = rows.indexOf(activeRow);
    const nextIndex = (() => {
      if (index === -1) {
        return 0;
      }
      if (direction === "next") {
        return Math.min(index + 1, rows.length - 1);
      }
      if (direction === "prev") {
        return Math.max(index - 1, 0);
      }
      return index;
    })();
    const next = rows[nextIndex];
    if (next) {
      setActiveRow(next);
      next.focus();
    }
  };

  document.addEventListener("click", (event) => {
    const row = event.target.closest("#manual-workspace-rows-body tr[data-tx-id]");
    if (row) {
      setActiveRow(row);
    }
  });

  document.addEventListener("click", (event) => {
    const entryButton = event.target.closest("[data-open-manual-entry='true']");
    if (!entryButton) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const row = entryButton.closest("#manual-workspace-rows-body tr[data-tx-id]");
    if (row) {
      setActiveRow(row);
      row.focus();
    }
    openEntryModal(row);
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const key = event.key.toLowerCase();

    if (key === "escape") {
      return;
    }

    if (isTypingContext(target)) {
      return;
    }

    if (key === "n") {
      event.preventDefault();
      openEntryModal(getActiveRow() || getRows()[0]);
      return;
    }

    if (key === "s") {
      event.preventDefault();
      const form = document.getElementById("manual-row-form");
      if (form) {
        form.requestSubmit ? form.requestSubmit() : form.submit();
      }
      return;
    }

    if (key === "f") {
      event.preventDefault();
      focusSearch();
      return;
    }

    if (key === "a") {
      event.preventDefault();
      triggerShortcutAction("adjust-orphan");
      return;
    }

    if (key === "m") {
      event.preventDefault();
      triggerShortcutAction("match-selected");
      return;
    }

    if (key === "u") {
      event.preventDefault();
      triggerShortcutAction("unmatch-selected");
      return;
    }

    if (key === "arrowright" || key === "arrowdown") {
      event.preventDefault();
      moveRowSelection("next");
      return;
    }

    if (key === "arrowleft" || key === "arrowup") {
      event.preventDefault();
      moveRowSelection("prev");
      return;
    }

    if (key === "enter") {
      event.preventDefault();
      moveRowSelection("next");
      return;
    }

    if (key === " " || key === "spacebar") {
      const activeRow = getActiveRow();
      if (activeRow) {
        event.preventDefault();
        activeRow.click();
      }
    }
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    if (!event.target) {
      return;
    }

    if (event.target.id === "manual-workspace-rows-body") {
      const latestRow = document.querySelector("#manual-workspace-rows-body tr[data-tx-id]:last-child");
      if (latestRow) {
        setActiveRow(latestRow);
      }
    }
  });

  window.ManualWorkspace = {
    setActiveRow,
  };

  document.addEventListener("DOMContentLoaded", () => {
    const firstRow = document.querySelector("#manual-workspace-rows-body tr[data-tx-id]");
    if (firstRow && !getActiveRow()) {
      setActiveRow(firstRow);
    }

    const modalEl = getEntryModal();
    if (modalEl) {
      modalEl.addEventListener("shown.bs.modal", () => {
        const firstField = getGridFields()[0];
        if (firstField) {
          firstField.focus();
          firstField.select?.();
        }
      });
    }
  });
})();

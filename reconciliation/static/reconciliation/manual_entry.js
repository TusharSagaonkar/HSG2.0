/**
 * Manual Bank Statement Entry — Excel-style Grid Controller
 *
 * Alpine.js component that manages the entire grid state, keyboard navigation,
 * client-side validation, shortcode expansion, paste parsing, and batch save
 * orchestration.
 *
 * This module is loaded from manual_entry.html and expects Alpine.js to be
 * available globally (loaded from CDN before this script runs).
 *
 * CSRF token is set on window.manualEntryCsrfToken by the Django template
 * before this script loads. All POST fetch requests include it in the
 * X-CSRFToken header.
 */
(function () {
  'use strict';

  // -----------------------------------------------------------------------
  // Constants
  // -----------------------------------------------------------------------

  const STORAGE_KEY_NARRATIONS = 'manual_entry_narrations';
  const MAX_NARRATION_HISTORY = 50;

  const FIELD_ORDER = ['date', 'narration', 'reference_no', 'debit', 'credit', 'balance'];

  const DEFAULT_SHORTCODES = {
    mc: 'Maintenance Collection',
    bc: 'Bank Charges',
    ic: 'Interest Credit',
    upi: 'UPI Collection',
    cd: 'Cheque Deposit',
    nc: 'NEFT Credit',
    rc: 'RTGS Credit',
  };

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

  /**
   * Retrieve a valid Django CSRF token from the nearest reliable source.
   */
  function getCsrfToken(form) {
    const isUsableToken = token => typeof token === 'string' && [32, 64].includes(token.length);

    const formToken = form?.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
    if (isUsableToken(formToken)) return formToken;

    if (isUsableToken(window.manualEntryCsrfToken)) return window.manualEntryCsrfToken;

    const cookie = document.cookie
      .split('; ')
      .find(row => row.startsWith('csrftoken='));
    const cookieToken = cookie ? decodeURIComponent(cookie.split('=').slice(1).join('=')) : '';
    return isUsableToken(cookieToken) ? cookieToken : '';
  }

  function csrfHeaders(form) {
    const token = getCsrfToken(form);
    return token ? { 'X-CSRFToken': token } : {};
  }

  function parseDecimal(val) {
    if (val === null || val === undefined || val === '') return null;
    const cleaned = String(val).replace(/,/g, '').trim();
    if (!cleaned || cleaned === '-') return null;
    const num = Number(cleaned);
    return Number.isFinite(num) ? num : null;
  }

  function parseDate(val) {
    if (!val) return null;
    const s = String(val).trim();
    // Try YYYY-MM-DD
    const isoMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (isoMatch) {
      const d = new Date(+isoMatch[1], +isoMatch[2] - 1, +isoMatch[3]);
      if (!isNaN(d.getTime())) return d;
    }
    // Try DD/MM/YYYY or DD-MM-YYYY
    const dmyMatch = s.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/);
    if (dmyMatch) {
      const year = dmyMatch[3].length === 2 ? 2000 + Number(dmyMatch[3]) : Number(dmyMatch[3]);
      const d = new Date(year, +dmyMatch[2] - 1, +dmyMatch[1]);
      if (!isNaN(d.getTime())) return d;
    }
    // Try paper statement formats like 01-May or 01 May 2026.
    const monthMatch = s.match(/^(\d{1,2})[\s\-]([A-Za-z]{3,9})(?:[\s\-](\d{4}))?$/);
    if (monthMatch) {
      const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
      const monthIdx = months.indexOf(monthMatch[2].slice(0, 3).toLowerCase());
      if (monthIdx >= 0) {
        const year = monthMatch[3] ? Number(monthMatch[3]) : new Date().getFullYear();
        const d = new Date(year, monthIdx, Number(monthMatch[1]));
        if (!isNaN(d.getTime())) return d;
      }
    }
    return null;
  }

  function normalizeDateForSave(val) {
    const parsed = parseDate(val);
    return parsed ? formatDateISO(parsed) : val;
  }

  function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return '0.00';
    return Number(val).toLocaleString('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function formatDateISO(d) {
    if (!d) return '';
    const date = d instanceof Date ? d : new Date(d);
    if (isNaN(date.getTime())) return '';
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  // -----------------------------------------------------------------------
  // Alpine.js Component
  // -----------------------------------------------------------------------

  if (typeof window !== 'undefined') {
    window.manualEntryGrid = function () {
      return {
        // ---- State ----
        rows: [],
        batchHeader: {
          bank_account: '',
          period_start: '',
          period_end: '',
          opening_balance: '',
          closing_balance: '',
        },
        selectedCell: { row: -1, field: '' },
        editingCell: { row: -1, field: '' },
        shortcodes: { ...DEFAULT_SHORTCODES },
        narrationHistory: [],
        isSaving: false,
        lastImportId: null,
        importDetailUrl: '',
        showPasteOverlay: false,
        validationTimer: null,
        bankAccountCount: 0,

        // ---- Computed (via getters in template) ----
        get stats() {
          let totalDebit = 0;
          let totalCredit = 0;
          let errorCount = 0;

          for (const row of this.rows) {
            const debit = parseDecimal(row.debit) || 0;
            const credit = parseDecimal(row.credit) || 0;
            totalDebit += debit;
            totalCredit += credit;

            if (row._errors && Object.keys(row._errors).length > 0) {
              errorCount++;
            }
          }

          const openingBalance = parseDecimal(this.batchHeader.opening_balance) || 0;
          const balanceDifference = openingBalance + totalCredit - totalDebit;

          return {
            totalDebit,
            totalCredit,
            balanceDifference,
            errorCount,
          };
        },

        get importSummary() {
          if (!this.lastImportId) return '';
          return `Import #${this.lastImportId} saved successfully.`;
        },

        // ---- Initialization ----
        async init() {
          this.bankAccountCount = Number(this.$el.dataset.bankAccountCount || 0);
          this.batchHeader.bank_account = this.$el.dataset.selectedBankAccount || '';
          this.initializeBankAccountSelection();
          await this.loadShortcodes();
          await this.loadNarrationHistory();
          this.addRow();
        },

        initializeBankAccountSelection() {
          const select = this.$refs.bankAccountSelect;
          if (!select || select.disabled) return;

          if (this.batchHeader.bank_account) {
            select.value = this.batchHeader.bank_account;
            return;
          }

          const selectableOptions = Array.from(select.options).filter(option => option.value);
          if (selectableOptions.length === 1) {
            this.batchHeader.bank_account = selectableOptions[0].value;
          }
        },

        onBankAccountChange(event) {
          const bankAccountId = event.target.value;
          this.batchHeader.bank_account = bankAccountId;
          const url = new URL(window.location.href);
          if (bankAccountId) {
            url.searchParams.set('bank_account', bankAccountId);
          } else {
            url.searchParams.delete('bank_account');
          }
          window.location.assign(url.toString());
        },

        async loadShortcodes() {
          try {
            const resp = await fetch(this.$el.dataset.shortcodesUrl || window.location.pathname.replace(/\/$/, '') + '/shortcodes/');
            if (resp.ok) {
              const data = await resp.json();
              if (data.shortcodes) {
                this.shortcodes = { ...DEFAULT_SHORTCODES, ...data.shortcodes };
              }
            }
          } catch (e) {
            // Use defaults
          }
        },

        async loadNarrationHistory() {
          try {
            const stored = localStorage.getItem(STORAGE_KEY_NARRATIONS);
            if (stored) {
              this.narrationHistory = JSON.parse(stored);
            }
            const url = this.$el.dataset.narrationsUrl;
            if (url) {
              const resp = await fetch(url);
              if (resp.ok) {
                const data = await resp.json();
                const serverNarrations = Array.isArray(data.narrations) ? data.narrations : [];
                this.narrationHistory = [
                  ...serverNarrations,
                  ...this.narrationHistory.filter(n => !serverNarrations.includes(n)),
                ].slice(0, MAX_NARRATION_HISTORY);
                this.saveNarrationHistory();
              }
            }
          } catch (e) {
            this.narrationHistory = this.narrationHistory || [];
          }
        },

        saveNarrationHistory() {
          try {
            localStorage.setItem(
              STORAGE_KEY_NARRATIONS,
              JSON.stringify(this.narrationHistory.slice(0, MAX_NARRATION_HISTORY))
            );
          } catch (e) {
            // localStorage may be full or unavailable
          }
        },

        trackNarration(narration) {
          if (!narration || narration.trim().length < 2) return;
          const trimmed = narration.trim();
          this.narrationHistory = [
            trimmed,
            ...this.narrationHistory.filter(n => n !== trimmed),
          ].slice(0, MAX_NARRATION_HISTORY);
          this.saveNarrationHistory();
        },

        // ---- Row Management ----
        createEmptyRow() {
          return {
            date: '',
            narration: '',
            reference_no: '',
            debit: '',
            credit: '',
            balance: '',
            _errors: {},
          };
        },

        addRow() {
          // Copy date from previous row if available
          const prevRow = this.rows.length > 0 ? this.rows[this.rows.length - 1] : null;
          const newRow = this.createEmptyRow();
          if (prevRow && prevRow.date) {
            newRow.date = prevRow.date;
          }
          this.rows.push(newRow);
          this.recalculateBalances();
          // Select the first cell of the new row
          this.$nextTick(() => {
            this.selectCell(this.rows.length - 1, 'date');
            this.focusCell(this.rows.length - 1, 'date');
          });
        },

        removeRow(idx) {
          if (this.rows.length <= 1) return;
          this.rows.splice(idx, 1);
          this.deselectAll();
          this.recalculateBalances();
        },

        // ---- Cell Selection & Editing ----
        selectCell(row, field) {
          this.selectedCell = { row, field };
          this.editingCell = { row: -1, field: '' };
          this.$nextTick(() => {
            this.focusCell(row, field);
          });
        },

        startEdit(row, field) {
          this.editingCell = { row, field };
          this.selectedCell = { row, field };
          this.$nextTick(() => {
            this.focusCell(row, field);
          });
        },

        deselectAll() {
          this.selectedCell = { row: -1, field: '' };
          this.editingCell = { row: -1, field: '' };
          if (document.activeElement) {
            document.activeElement.blur();
          }
        },

        isSelected(row, field) {
          return this.selectedCell.row === row && this.selectedCell.field === field;
        },

        isEditing(row, field) {
          return this.editingCell.row === row && this.editingCell.field === field;
        },

        focusCell(row, field) {
          const input = document.querySelector(
            `[data-grid-row="${row}"][data-grid-field="${field}"]`
          );
          if (input) {
            input.focus();
            if (input.tagName === 'INPUT' && input.type !== 'number') {
              input.select();
            }
          }
        },

        // ---- Keyboard Navigation ----
        handleKeydown(e) {
          const { row, field } = this.editingCell.row >= 0 ? this.editingCell : this.selectedCell;

          // Ctrl+S: Save batch
          if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            this.saveBatch();
            return;
          }

          // Ctrl+N: Add new row
          if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
            e.preventDefault();
            this.addRow();
            return;
          }

          // Escape: Deselect
          if (e.key === 'Escape') {
            this.deselectAll();
            return;
          }

          // Delete: Clear cell
          if (e.key === 'Delete' && row >= 0 && field) {
            e.preventDefault();
            this.clearCell(row, field);
            return;
          }

          // Only navigate if we have a selected cell
          if (row < 0 || !field) return;

          const fieldIdx = FIELD_ORDER.indexOf(field);
          if (fieldIdx < 0) return;

          let handled = false;

          switch (e.key) {
            case 'ArrowUp':
              e.preventDefault();
              this.navigateTo(row - 1, fieldIdx);
              handled = true;
              break;

            case 'ArrowDown':
              e.preventDefault();
              this.navigateTo(row + 1, fieldIdx);
              handled = true;
              break;

            case 'ArrowLeft':
              e.preventDefault();
              if (fieldIdx > 0) {
                this.navigateTo(row, fieldIdx - 1);
              } else if (row > 0) {
                this.navigateTo(row - 1, FIELD_ORDER.length - 1);
              }
              handled = true;
              break;

            case 'ArrowRight':
              e.preventDefault();
              if (fieldIdx < FIELD_ORDER.length - 1) {
                this.navigateTo(row, fieldIdx + 1);
              } else if (row < this.rows.length - 1) {
                this.navigateTo(row + 1, 0);
              }
              handled = true;
              break;

            case 'Tab':
              e.preventDefault();
              if (e.shiftKey) {
                // Shift+Tab: previous field
                if (fieldIdx > 0) {
                  this.navigateTo(row, fieldIdx - 1);
                } else if (row > 0) {
                  this.navigateTo(row - 1, FIELD_ORDER.length - 1);
                }
              } else {
                // Tab: next field
                if (fieldIdx < FIELD_ORDER.length - 1) {
                  this.navigateTo(row, fieldIdx + 1);
                } else if (row < this.rows.length - 1) {
                  this.navigateTo(row + 1, 0);
                } else {
                  this.addRow();
                }
              }
              handled = true;
              break;

            case 'Enter':
              e.preventDefault();
              if (e.shiftKey) {
                // Shift+Enter: previous row
                this.navigateTo(row - 1, fieldIdx);
              } else {
                // Enter: next row or next field
                if (fieldIdx < FIELD_ORDER.length - 1) {
                  this.navigateTo(row, fieldIdx + 1);
                } else if (row < this.rows.length - 1) {
                  this.navigateTo(row + 1, 0);
                } else {
                  this.addRow();
                }
              }
              handled = true;
              break;
          }

          if (handled) {
            // After navigation, validate the row we left
            this.scheduleValidation(row);
          }
        },

        navigateTo(row, fieldIdx) {
          if (row < 0 || row >= this.rows.length) return;
          const field = FIELD_ORDER[fieldIdx];
          if (!field) return;
          this.startEdit(row, field);
        },

        clearCell(row, field) {
          if (row >= 0 && row < this.rows.length && this.rows[row]) {
            this.rows[row][field] = '';
            this.rows[row]._errors = this.rows[row]._errors || {};
            delete this.rows[row]._errors[field];
            this.recalculateBalances();
          }
        },

        // ---- Cell Event Handlers ----
        onCellFocus(row, field) {
          this.startEdit(row, field);
        },

        onCellBlur(row, field) {
          // Track narration
          if (field === 'narration' && this.rows[row]) {
            this.trackNarration(this.rows[row].narration);
          }
          // Validate on blur
          this.scheduleValidation(row);
        },

        onCellInput(row, field) {
          // Clear row errors on input change
          if (this.rows[row] && this.rows[row]._errors) {
            delete this.rows[row]._errors[field];
          }
          this.recalculateBalances();
        },

        scheduleValidation(row) {
          if (this.validationTimer) {
            clearTimeout(this.validationTimer);
          }
          this.validationTimer = setTimeout(() => {
            this.validateRow(row);
          }, 300);
        },

        // ---- Client-Side Validation ----
        validateRow(row) {
          if (row < 0 || row >= this.rows.length) return;
          const data = this.rows[row];
          const errors = {};

          // Date validation
          if (!data.date || !data.date.trim()) {
            errors.date = 'Date is required';
          } else {
            const parsed = parseDate(data.date);
            if (!parsed) {
              errors.date = 'Invalid date format (use DD/MM/YYYY)';
            }
          }

          // Narration validation
          if (!data.narration || !data.narration.trim()) {
            errors.narration = 'Narration is required';
          }

          // Debit/Credit validation
          const debit = parseDecimal(data.debit);
          const credit = parseDecimal(data.credit);

          if (debit && credit && debit > 0 && credit > 0) {
            errors.amount = 'Enter either debit or credit, not both';
          } else if (!debit && !credit) {
            errors.amount = 'Enter a debit or credit amount';
          } else if (debit && debit <= 0) {
            errors.debit = 'Debit must be positive';
          } else if (credit && credit <= 0) {
            errors.credit = 'Credit must be positive';
          }

          this.validateDuplicate(row, errors);
          this.validateBalanceContinuity(row, errors);
          data._errors = errors;

          // Clear validation error class messages to avoid clutter
          if (errors.amount) {
            data._errors.debit = data._errors.debit || errors.amount;
            data._errors.credit = data._errors.credit || errors.amount;
          }
        },

        rowSignature(row) {
          const parsedDate = parseDate(row.date);
          const debit = parseDecimal(row.debit) || 0;
          const credit = parseDecimal(row.credit) || 0;
          const amount = credit > 0 ? credit : debit;
          if (!parsedDate || !amount || !row.narration) return '';
          return [
            formatDateISO(parsedDate),
            amount.toFixed(2),
            String(row.narration).trim().toLowerCase(),
            String(row.reference_no || '').trim().toLowerCase(),
          ].join('|');
        },

        validateDuplicate(rowIdx, errors) {
          const signature = this.rowSignature(this.rows[rowIdx]);
          if (!signature) return;
          const firstIdx = this.rows.findIndex((row, idx) => idx !== rowIdx && this.rowSignature(row) === signature);
          if (firstIdx >= 0) {
            errors.duplicate = `Possible duplicate of row ${firstIdx + 1}`;
            errors.reference_no = errors.reference_no || errors.duplicate;
          }
        },

        validateBalanceContinuity(rowIdx, errors) {
          const supplied = parseDecimal(this.rows[rowIdx].balance);
          if (supplied === null) return;
          const expected = Number(this.computeBalance(rowIdx));
          if (Math.abs(supplied - expected) >= 0.01) {
            errors.balance = `Expected balance ${expected.toFixed(2)}`;
          }
        },

        validateAllRows() {
          for (let i = 0; i < this.rows.length; i++) {
            this.validateRow(i);
          }
          const hasErrors = this.rows.some(
            r => r._errors && Object.keys(r._errors).length > 0
          );
          return !hasErrors;
        },

        // ---- Shortcode Expansion ----
        expandShortcode(row, e) {
          if (e.key !== 'Tab' && e.key !== ' ') return;
          if (!this.shortcodes) return;
          if (row < 0 || row >= this.rows.length) return;

          const input = e.target;
          const val = (input.value || '').trim().toLowerCase();

          if (this.shortcodes[val]) {
            e.preventDefault();
            this.rows[row].narration = this.shortcodes[val];
            input.value = this.shortcodes[val];
          }
        },

        // ---- Balance Calculation ----
        computeBalance(rowIdx, sourceRows = this.rows) {
          if (rowIdx < 0 || rowIdx >= sourceRows.length) return '';

          const openingBalance = parseDecimal(this.batchHeader.opening_balance) || 0;
          let balance = openingBalance;

          for (let i = 0; i <= rowIdx; i++) {
            const row = sourceRows[i];
            const debit = parseDecimal(row.debit) || 0;
            const credit = parseDecimal(row.credit) || 0;
            balance = balance - debit + credit;
          }

          return balance.toFixed(2);
        },

        recalculateBalances() {
          // Balance is computed in computeBalance() — Alpine reactivity handles this
          // Force an update by touching the rows
          this.rows = [...this.rows];
        },

        // ---- Paste Support ----
        handlePaste(e) {
          // Only handle paste if we're not focused on a specific input, or if focus is on the grid wrapper
          const activeEl = document.activeElement;
          if (activeEl && activeEl.tagName === 'INPUT' && activeEl.closest('.grid-cell-input')) {
            const text = (e.clipboardData || window.clipboardData)?.getData('text/plain') || '';
            if (!text.includes('\n') && !text.includes('\t')) return;
          }

          e.preventDefault();
          const clipboardData = e.clipboardData || window.clipboardData;
          if (!clipboardData) return;

          const text = clipboardData.getData('text/plain');
          if (!text || !text.trim()) return;

          this.parsePastedData(text);
        },

        parsePastedData(text) {
          const lines = text.trim().split(/\r?\n/);
          if (lines.length === 0) return;

          // Determine delimiter: tab or comma
          const firstLine = lines[0];
          const delimiter = firstLine.includes('\t') ? '\t' : ',';

          const parsedRows = [];
          let hasHeader = false;
          const headerLower = firstLine.toLowerCase();

          // Detect header row
          if (
            headerLower.includes('date') ||
            headerLower.includes('narration') ||
            headerLower.includes('debit') ||
            headerLower.includes('credit')
          ) {
            hasHeader = true;
          }

          const startIdx = hasHeader ? 1 : 0;

          for (let i = startIdx; i < lines.length; i++) {
            const line = lines[i].trim();
            if (!line) continue;

            const cells = line.split(delimiter).map(c => c.trim().replace(/^"|"$/g, ''));
            if (cells.length < 2) continue;

            const row = this.createEmptyRow();

            if (hasHeader) {
              // Map by header
              const headers = firstLine.split(delimiter).map(h => h.trim().toLowerCase().replace(/^"|"$/g, ''));
              for (let j = 0; j < headers.length && j < cells.length; j++) {
                const h = headers[j];
                const val = cells[j];
                if (h === 'date' || h === 'value date') row.date = val;
                else if (h === 'narration' || h === 'description' || h === 'particulars') row.narration = val;
                else if (h === 'ref' || h === 'reference' || h === 'ref no' || h === 'reference no' || h === 'cheque' || h === 'chq/ref number') row.reference_no = val;
                else if (h === 'debit' || h === 'withdrawal') row.debit = val;
                else if (h === 'credit' || h === 'deposit') row.credit = val;
                else if (h === 'balance' || h === 'closing balance') row.balance = val;
              }
            } else {
              // Positional: Date, Narration, Reference, Debit, Credit, Balance.
              // If only 3 columns exist, treat the amount as Credit for fast paper entry.
              if (cells.length >= 1) row.date = cells[0];
              if (cells.length >= 2) row.narration = cells[1];
              if (cells.length === 3) {
                row.credit = cells[2];
              } else {
                if (cells.length >= 3) row.reference_no = cells[2];
                if (cells.length >= 4) row.debit = cells[3];
                if (cells.length >= 5) row.credit = cells[4];
                if (cells.length >= 6) row.balance = cells[5];
              }
            }

            // Validate the parsed row
            this.validateRowData(row);
            parsedRows.push(row);
          }

          if (parsedRows.length > 0) {
            const existingRows = this.rows.length === 1 && this.isRowEmpty(this.rows[0]) ? [] : this.rows;
            this.rows = [...existingRows, ...parsedRows];
            this.recalculateBalances();
            this.showPasteOverlay = false;

            // Show feedback
            this.showToast(
              `Pasted ${parsedRows.length} row(s) successfully.`,
              'success'
            );
          }
        },

        validateRowData(row) {
          row._errors = {};
          if (!row.date || !row.date.trim()) {
            row._errors.date = 'Date is required';
          } else if (!parseDate(row.date)) {
            row._errors.date = 'Invalid date format';
          }
          if (!row.narration || !row.narration.trim()) {
            row._errors.narration = 'Narration is required';
          }
          const debit = parseDecimal(row.debit);
          const credit = parseDecimal(row.credit);
          if (debit && credit && debit > 0 && credit > 0) {
            row._errors.amount = 'Both debit and credit';
          }
          if (!debit && !credit) {
            row._errors.amount = 'Enter a debit or credit amount';
          }
        },

        isRowEmpty(row) {
          return !row.date && !row.narration && !row.reference_no && !row.debit && !row.credit;
        },

        // ---- Batch Save ----
        async saveBatch() {
          // Validate batch header
          if (this.bankAccountCount === 0) {
            this.showToast('No bank account is available for the selected society.', 'error');
            return;
          }
          if (!this.batchHeader.bank_account) {
            this.showToast('Please select a bank account.', 'error');
            this.$refs.bankAccountSelect?.focus();
            return;
          }
          if (!this.batchHeader.period_start || !this.batchHeader.period_end) {
            this.showToast('Please set the statement period.', 'error');
            return;
          }
          if (this.batchHeader.opening_balance === '' || this.batchHeader.opening_balance === null) {
            this.showToast('Please enter the opening balance.', 'error');
            return;
          }

          // Validate all rows
          if (this.rows.length === 0) {
            this.showToast('No rows to save. Add at least one row.', 'error');
            return;
          }

          if (!this.validateAllRows()) {
            this.showToast('Please fix validation errors before saving.', 'error');
            return;
          }

          this.isSaving = true;

          // Build rows data from the compact non-empty row set to preserve balance order.
          const nonEmptyRows = this.rows.filter(row => !this.isRowEmpty(row));
          const rowsData = nonEmptyRows.map((row, idx) => ({
            date: normalizeDateForSave(row.date),
            narration: row.narration,
            reference_no: row.reference_no,
            debit: row.debit || '0',
            credit: row.credit || '0',
            balance: this.computeBalance(idx, nonEmptyRows),
          }));

          const formData = new FormData();
          formData.append('bank_account', this.batchHeader.bank_account);
          formData.append('period_start', this.batchHeader.period_start);
          formData.append('period_end', this.batchHeader.period_end);
          formData.append('opening_balance', this.batchHeader.opening_balance);
          formData.append('closing_balance', this.batchHeader.closing_balance || '');
          formData.append('rows', JSON.stringify(rowsData));

          try {
            const resp = await fetch(this.$el.dataset.saveUrl || window.location.pathname.replace(/\/$/, '') + '/batch/save/', {
              method: 'POST',
              body: formData,
              headers: {
                'X-Requested-With': 'XMLHttpRequest',
                ...csrfHeaders(),
              },
            });

            const data = await resp.json();

            if (resp.ok) {
              this.lastImportId = data.import_id;
              this.importDetailUrl = data.redirect_url || '';
              this.showToast(
                `Batch saved: ${data.transaction_count} transaction(s) created.`,
                'success'
              );
              // Clear rows after successful save
              this.rows = [];
              this.addRow();
            } else {
              const errorMsg = data.error || 'Save failed.';
              this.showToast(errorMsg, 'error');

              // If row-level errors returned, apply them
              if (data.row_errors && Array.isArray(data.row_errors)) {
                for (const rowErr of data.row_errors) {
                  const idx = (rowErr._row_index || 0) - 1;
                  if (idx >= 0 && idx < this.rows.length) {
                    this.rows[idx]._errors = rowErr;
                  }
                }
              }
            }
          } catch (err) {
            this.showToast('Network error: ' + err.message, 'error');
          } finally {
            this.isSaving = false;
          }
        },

        async saveVoucherMatch(event) {
          const form = event.target;
          const submitButton = form.querySelector('button[type="submit"]');
          const originalButtonText = submitButton ? submitButton.textContent : '';
          const formData = new FormData(form);

          if (!formData.get('transaction_date')) {
            this.showToast('Please enter bank transaction date.', 'error');
            return;
          }

          if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = 'Saving...';
          }

          try {
            const matchUrl = form.action || this.$el.dataset.voucherMatchUrl;
            const resp = await fetch(matchUrl, {
              method: 'POST',
              body: formData,
              headers: {
                'X-Requested-With': 'XMLHttpRequest',
                ...csrfHeaders(form),
              },
            });
            const contentType = resp.headers.get('content-type') || '';
            const data = contentType.includes('application/json') ? await resp.json() : {};
            if (!resp.ok) {
              this.showToast(data.error || `Manual match failed (${resp.status}).`, 'error');
              if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = originalButtonText;
              }
              return;
            }
            this.showToast(data.message || 'Bank entry created and reconciled.', 'success');
            setTimeout(() => window.location.reload(), 350);
          } catch (err) {
            this.showToast('Unable to process manual match response: ' + err.message, 'error');
            if (submitButton) {
              submitButton.disabled = false;
              submitButton.textContent = originalButtonText;
            }
          }
        },

        // ---- Toast Notifications ----
        showToast(message, type) {
          // Simple toast: use Bootstrap toast if available, otherwise alert
          const toastContainer = document.getElementById('manual-entry-toast-container');
          if (!toastContainer) {
            // Create a simple toast container
            const container = document.createElement('div');
            container.id = 'manual-entry-toast-container';
            container.className = 'toast-container position-fixed top-0 end-0 p-3';
            container.style.zIndex = '9999';
            document.body.appendChild(container);
          }

          const bgClass = type === 'success' ? 'bg-success' : 'bg-danger';
          const icon = type === 'success' ? 'check-circle' : 'exclamation-circle';

          const toastEl = document.createElement('div');
          toastEl.className = `toast align-items-center text-white ${bgClass} border-0`;
          toastEl.setAttribute('role', 'alert');
          toastEl.innerHTML = `
            <div class="d-flex">
              <div class="toast-body">
                <i class="bi bi-${icon} me-2"></i>${message}
              </div>
              <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
          `;

          const container = document.getElementById('manual-entry-toast-container');
          container.appendChild(toastEl);

          // Use Bootstrap toast if available
          if (typeof bootstrap !== 'undefined' && bootstrap.Toast) {
            const bsToast = new bootstrap.Toast(toastEl, { delay: 4000 });
            bsToast.show();
            toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
          } else {
            // Fallback: auto-remove after 4 seconds
            setTimeout(() => {
              toastEl.remove();
            }, 4000);
          }
        },

        // ---- Utility ----
        formatCurrency,
      };
    };
  }
})();
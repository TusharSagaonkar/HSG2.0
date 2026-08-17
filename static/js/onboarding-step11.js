document.addEventListener("DOMContentLoaded", function () {
  // After adding a row, focus the first editable input in the newly-added row.
  var addBtn = document.getElementById("manualAddRow");
  var bodyEl = document.getElementById("manualRowsBody");

  function focusLastRowFirstInput() {
    if (!bodyEl) return;
    var rows = bodyEl.querySelectorAll(".manual-row");
    if (!rows.length) return;
    var lastRow = rows[rows.length - 1];
    var input = lastRow.querySelector("input:not([readonly]):not([disabled]), select:not([disabled]), textarea:not([disabled])");
    if (input) {
      input.focus();
      if (input.select) input.select();
    }
  }

  if (addBtn) {
    addBtn.addEventListener("click", function () {
      // small delay to let the existing inline addRow handler finish
      setTimeout(focusLastRowFirstInput, 80);
    });
  }
});

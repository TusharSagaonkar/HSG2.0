/**
 * Component Enhancements
 * Handles interactive behavior for UI components
 */

class ComponentManager {
  constructor() {
    this.initActionBars();
    this.initDropdowns();
    this.initTooltips();
    this.initFormEnhancements();
  }

  /**
   * Initialize responsive action bars
   */
  initActionBars() {
    const actionBars = document.querySelectorAll('.action-bar--responsive');
    
    actionBars.forEach(bar => {
      const actions = bar.querySelectorAll('.btn-action, .btn-action-icon');
      if (actions.length <= 3) return;

      // Create mobile dropdown for extra actions
      const dropdown = document.createElement('div');
      dropdown.className = 'action-bar__dropdown';
      
      const toggle = document.createElement('button');
      toggle.className = 'btn-action btn-action--secondary action-bar__mobile-toggle';
      toggle.innerHTML = '<i class="fas fa-ellipsis-v"></i> More';
      
      // Move extra actions to dropdown (keep first 2 visible)
      const extraActions = Array.from(actions).slice(2);
      extraActions.forEach(action => {
        const clone = action.cloneNode(true);
        clone.className = 'btn-action btn-action--secondary';
        dropdown.appendChild(clone);
        action.style.display = 'none';
      });

      toggle.addEventListener('click', (e) => {
        e.preventDefault();
        dropdown.classList.toggle('show');
      });

      // Close on outside click
      document.addEventListener('click', (e) => {
        if (!bar.contains(e.target)) {
          dropdown.classList.remove('show');
        }
      });

      bar.appendChild(toggle);
      bar.appendChild(dropdown);
    });
  }

  /**
   * Initialize custom dropdowns
   */
  initDropdowns() {
    const dropdowns = document.querySelectorAll('[data-dropdown]');
    
    dropdowns.forEach(trigger => {
      const targetId = trigger.getAttribute('data-dropdown');
      const menu = document.getElementById(targetId);
      if (!menu) return;

      trigger.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        menu.classList.toggle('show');
        trigger.setAttribute('aria-expanded', menu.classList.contains('show'));
      });

      // Close on outside click
      document.addEventListener('click', (e) => {
        if (!trigger.contains(e.target) && !menu.contains(e.target)) {
          menu.classList.remove('show');
          trigger.setAttribute('aria-expanded', 'false');
        }
      });
    });
  }

  /**
   * Initialize tooltips
   */
  initTooltips() {
    const tooltipElements = document.querySelectorAll('[data-tooltip]');
    
    tooltipElements.forEach(element => {
      element.addEventListener('mouseenter', (e) => {
        const text = element.getAttribute('data-tooltip');
        if (!text) return;

        const tooltip = document.createElement('div');
        tooltip.className = 'custom-tooltip';
        tooltip.textContent = text;
        document.body.appendChild(tooltip);

        const rect = element.getBoundingClientRect();
        tooltip.style.left = `${rect.left + rect.width / 2 - tooltip.offsetWidth / 2}px`;
        tooltip.style.top = `${rect.top - tooltip.offsetHeight - 8}px`;
        
        element._tooltip = tooltip;
      });

      element.addEventListener('mouseleave', () => {
        if (element._tooltip) {
          element._tooltip.remove();
          element._tooltip = null;
        }
      });
    });
  }

  /**
   * Form enhancements
   */
  initFormEnhancements() {
    // Add loading state to submit buttons
    const forms = document.querySelectorAll('form[data-loading-state]');
    
    forms.forEach(form => {
      form.addEventListener('submit', (e) => {
        const submitBtn = form.querySelector('[type="submit"]');
        if (submitBtn && !submitBtn.disabled) {
          submitBtn.classList.add('btn--loading');
          submitBtn.disabled = true;
          
          // Re-enable after timeout (fallback)
          setTimeout(() => {
            submitBtn.classList.remove('btn--loading');
            submitBtn.disabled = false;
          }, 10000);
        }
      });
    });

    // Confirm navigation for forms with unsaved changes
    const formsWithConfirm = document.querySelectorAll('form[data-confirm-navigate]');
    
    formsWithConfirm.forEach(form => {
      let formChanged = false;
      
      form.addEventListener('input', () => {
        formChanged = true;
      });

      form.addEventListener('submit', () => {
        formChanged = false;
      });

      window.addEventListener('beforeunload', (e) => {
        if (formChanged) {
          e.preventDefault();
          e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
          return e.returnValue;
        }
      });
    });
  }
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    new ComponentManager();
  });
} else {
  new ComponentManager();
}

export default ComponentManager;

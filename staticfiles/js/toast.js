/**
 * Toast Notification System
 * Provides non-intrusive feedback for user actions
 */

class ToastManager {
  constructor() {
    this.container = null;
    this.position = 'top-right';
    this.defaultDuration = 3000;
    this.maxToasts = 5;
    this.init();
  }

  /**
   * Initialize the toast container
   */
  init() {
    this.container = document.getElementById('toast-container');
    if (!this.container) {
      this.container = document.createElement('div');
      this.container.id = 'toast-container';
      this.container.className = 'toast-container';
      document.body.appendChild(this.container);
    }
  }

  /**
   * Show a toast notification
   * @param {string} message - The message to display
   * @param {string} type - Type of toast: 'success', 'error', 'warning', 'info'
   * @param {number} duration - Duration in ms (0 for persistent)
   * @param {string} title - Optional title
   * @returns {HTMLElement} The toast element
   */
  show(message, type = 'info', duration = this.defaultDuration, title = '') {
    // Limit number of toasts
    while (this.container.children.length >= this.maxToasts) {
      const oldest = this.container.firstElementChild;
      if (oldest) {
        oldest.remove();
      }
    }

    const toast = this.createToastElement(message, type, title);
    this.container.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => {
      toast.classList.add('show');
    });

    // Auto-dismiss
    if (duration > 0) {
      this.autoDismiss(toast, duration);
    }

    return toast;
  }

  /**
   * Create toast element
   */
  createToastElement(message, type, title) {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;

    const icons = {
      success: 'fa-check-circle',
      error: 'fa-exclamation-circle',
      warning: 'fa-exclamation-triangle',
      info: 'fa-info-circle',
    };

    const icon = icons[type] || icons.info;

    toast.innerHTML = `
      <div class="toast__icon">
        <i class="fas ${icon}"></i>
      </div>
      <div class="toast__content">
        ${title ? `<div class="toast__title">${this.escapeHtml(title)}</div>` : ''}
        <div class="toast__message">${this.escapeHtml(message)}</div>
      </div>
      <button class="toast__close" aria-label="Close notification">
        <i class="fas fa-times"></i>
      </button>
      <div class="toast__progress"></div>
    `;

    // Close button handler
    const closeBtn = toast.querySelector('.toast__close');
    closeBtn.addEventListener('click', () => this.dismiss(toast));

    return toast;
  }

  /**
   * Auto-dismiss with progress bar
   */
  autoDismiss(toast, duration) {
    const progressBar = toast.querySelector('.toast__progress');
    if (progressBar) {
      progressBar.style.animationDuration = `${duration}ms`;
      progressBar.classList.add('active');
    }

    setTimeout(() => {
      this.dismiss(toast);
    }, duration);
  }

  /**
   * Dismiss a toast with animation
   */
  dismiss(toast) {
    if (!toast || toast.classList.contains('dismissing')) {
      return;
    }

    toast.classList.add('dismissing');
    toast.classList.remove('show');

    setTimeout(() => {
      toast.remove();
    }, 300);
  }

  /**
   * Convenience methods
   */
  success(message, duration, title) {
    return this.show(message, 'success', duration, title || 'Success');
  }

  error(message, duration, title) {
    return this.show(message, 'error', duration || 5000, title || 'Error');
  }

  warning(message, duration, title) {
    return this.show(message, 'warning', duration, title || 'Warning');
  }

  info(message, duration, title) {
    return this.show(message, 'info', duration, title || 'Info');
  }

  /**
   * Clear all toasts
   */
  clearAll() {
    while (this.container.firstElementChild) {
      this.container.firstElementChild.remove();
    }
  }

  /**
   * Escape HTML to prevent XSS
   */
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}

// Initialize on DOM ready
let toastManager = null;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    toastManager = new ToastManager();
    window.toast = toastManager;
  });
} else {
  toastManager = new ToastManager();
  window.toast = toastManager;
}

export default ToastManager;

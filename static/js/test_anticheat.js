/**
 * Anti-Cheat Module for NMT style testing
 */

class TestAntiCheat {
    constructor(options = {}) {
        this.maxViolations = options.maxViolations || 3;
        this.violations = 0;
        this.onViolation = options.onViolation || function() {};
        this.onDisqualify = options.onDisqualify || function() {};
        this.init();
    }

    init() {
        // Prevent right click context menu
        document.addEventListener('contextmenu', e => e.preventDefault());

        // Track tab switches
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.recordViolation('Зміна вкладки або мінімізація браузера');
            }
        });

        // Track window blur
        window.addEventListener('blur', () => {
            this.recordViolation('Втрата фокусу вікна тестування');
        });

        // Block copy shortcuts
        document.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && ['c', 'v', 'a', 'x', 'p', 'u', 's'].includes(e.key.toLowerCase())) {
                e.preventDefault();
            }
        });
    }

    recordViolation(reason) {
        this.violations++;
        this.onViolation(this.violations, reason);
        if (this.violations >= this.maxViolations) {
            this.onDisqualify();
        }
    }
}

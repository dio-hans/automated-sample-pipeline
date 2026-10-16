document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (event.defaultPrevented) return;
    if (form.dataset.submitGuard === "inline") return;

    if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
    }

    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");

    const submitter = event.submitter;
    const indicatorTarget = submitter || form.querySelector(
        'button:not([type]), button[type="submit"], input[type="submit"]'
    );

    if (indicatorTarget) {
        indicatorTarget.setAttribute("aria-disabled", "true");
        indicatorTarget.setAttribute("aria-busy", "true");
        indicatorTarget.dataset.submitting = "true";

        if (indicatorTarget instanceof HTMLButtonElement) {
            const status = document.createElement("span");
            status.className = "submit-progress";
            status.setAttribute("aria-hidden", "true");
            status.textContent = "Submitting...";
            indicatorTarget.appendChild(status);
        } else if (indicatorTarget.parentElement) {
            const status = document.createElement("span");
            status.className = "submit-progress";
            status.setAttribute("role", "status");
            status.textContent = "Submitting...";
            indicatorTarget.insertAdjacentElement("afterend", status);
        }
    }

    form.querySelectorAll(
        'button:not([type]), button[type="submit"], input[type="submit"], input[type="image"]'
    ).forEach(function (control) {
        if (control !== submitter) {
            control.disabled = true;
        }
    });
});

document.addEventListener("DOMContentLoaded", function () {
    const sidebar = document.getElementById("mainSidebar");
    const toggle = document.getElementById("sidebarToggle");
    const overlay = document.getElementById("sidebarOverlay");
    
    if (!sidebar || !toggle || !overlay) return;

    function openSidebar() {
        sidebar.classList.remove("-translate-x-full");
        overlay.classList.remove("hidden");
        document.body.classList.add("overflow-hidden");
    }

    function closeSidebar() {
        sidebar.classList.add("-translate-x-full");
        overlay.classList.add("hidden");
        document.body.classList.remove("overflow-hidden");
    }

    toggle.addEventListener("click", openSidebar);
    overlay.addEventListener("click", closeSidebar);

    sidebar.querySelectorAll("a").forEach(function (link) {
        link.addEventListener("click", function () {
            if (window.innerWidth < 1024) {
                closeSidebar();
            }
        });
    });

    window.addEventListener("resize", function () {
        if (window.innerWidth >= 1024) {
            overlay.classList.add("hidden");
            document.body.classList.remove("overflow-hidden");
        }
    });
});

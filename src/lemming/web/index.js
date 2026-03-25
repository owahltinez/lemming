(async () => {
  // Mancha.initMancha is the robust way to initialize.
  window.ManchaApp = Mancha.initMancha({
    cloak: true,
    callback: async (renderer) => {
      const { $ } = renderer;

      // --- Project Scoping ---
      // $$project is auto-synced with the ?project= URL query param by mancha.
      $.$$project = $.$$project ?? "";

      // Helper: build a URL with the project query param.
      function apiUrl(path, extraParams = {}) {
        const params = new URLSearchParams(extraParams);
        if ($.$$project) params.set("project", $.$$project);
        const qs = params.toString();
        return qs ? `${path}?${qs}` : path;
      }

      // --- Persistence Management (scoped by project) ---
      const storagePrefix = $.$$project
        ? `lemming[${$.$$project}]_`
        : "lemming_";
      const Storage = {
        get(key, fallback) {
          try {
            const val = localStorage.getItem(storagePrefix + key);
            return val !== null ? JSON.parse(val) : fallback;
          } catch {
            return fallback;
          }
        },
        set(key, val) {
          localStorage.setItem(storagePrefix + key, JSON.stringify(val));
        },
      };

      // --- Initial State ---
      $.tasks = [];
      $.context = "";
      $.cwd = "";
      $.newTask = "";
      $.loading = true;
      $.runners = [];
      $.selectedRunner = Storage.get("selected_runner", "gemini");
      $.retries = Storage.get("retries", 3);
      $.envOverrides = []; // Will hydrate below
      $.hideCompleted = Storage.get("hide_completed", false);
      $.reviewEnabled = Storage.get("review_enabled", true);
      $.toasts = [];
      $.expanded = {};
      $.loopRunning = false;
      $.editingTask = null;
      $.editFormData = { description: "", runner: "", parent: "" };

      // --- Favicon Status ---
      $.faviconState = "idle";
      $.lastSeenState = null;

      // --- Folder Picker State ---
      $.folderPickerPath = "";
      $.folderPickerDirs = [];
      $.folderPickerLoading = false;

      // --- Computed Properties ---
      $.completedCount = $.$computed(
        ($) => $.tasks.filter((t) => t.status === "completed").length,
      );

      $.filteredTasks = $.$computed(($) => {
        const ts = $.tasks;
        const inProgress = ts.filter((t) => t.status === "in_progress");
        const pending = ts.filter((t) => t.status === "pending");
        const completed = ts
          .filter((t) => t.status === "completed" && !$.hideCompleted)
          .sort((a, b) => (b.completed_at || 0) - (a.completed_at || 0));
        return [...inProgress, ...pending, ...completed];
      });

      // --- Utilities ---
      $.trim = (s, l = 60) =>
        s && s.length > l ? `${s.substring(0, l - 3)}...` : s;
      $.formatDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "");
      $.formatDuration = (seconds) => {
        if (!seconds) return "0s";
        if (seconds < 60) return `${Math.floor(seconds)}s`;
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = Math.floor(seconds % 60);
        return `${minutes}m ${remainingSeconds}s`;
      };
      $.formatTaskRunTime = (task) => {
        let total = task.run_time || 0;
        if (task.status === "in_progress" && task.started_at) {
          total += Date.now() / 1000 - task.started_at;
        }
        return $.formatDuration(total);
      };

      $.getParent = (parentId) => {
        return $.tasks.find((t) => t.id === parentId);
      };

      $.copyToClipboard = function (text) {
        if (!navigator.clipboard) {
          const el = document.createElement("textarea");
          el.value = text;
          document.body.appendChild(el);
          el.select();
          document.execCommand("copy");
          document.body.removeChild(el);
        } else {
          navigator.clipboard.writeText(text);
        }
        this.addToast("Copied to clipboard", "info");
      };

      // --- UI Feedback ---
      $.addToast = function (message, type = "info") {
        const id = Date.now() + Math.random();
        this.toasts.push({ id, message: this.trim(message, 120), type });
        setTimeout(() => {
          this.toasts = this.toasts.filter((t) => t.id !== id);
        }, 5000);
      };

      // --- Data Actions ---
      $.fetchData = async function () {
        const response = await fetch(apiUrl("/api/data"));
        if (!response.ok) return;
        const data = await response.json();

        this.cwd = data.cwd || "";
        this.loopRunning = data.loop_running || false;
        const newTasks = data.tasks || [];

        // Show toast notifications for task state changes.
        if (!this.loading && this.tasks.length > 0) {
          const oldTaskMap = new Map(this.tasks.map((t) => [t.id, t]));
          for (const newTask of newTasks) {
            const oldTask = oldTaskMap.get(newTask.id);
            if (!oldTask) continue;
            if (
              oldTask.status !== "completed" &&
              newTask.status === "completed"
            ) {
              this.addToast(
                `Task completed: ${this.trim(newTask.description, 60)}`,
                "success",
              );
            } else if (
              oldTask.status === "in_progress" &&
              newTask.status === "pending"
            ) {
              this.addToast(
                `Task failed: ${this.trim(newTask.description, 60)}`,
                "error",
              );
            } else if (
              (newTask.outcomes?.length || 0) > (oldTask.outcomes?.length || 0)
            ) {
              this.addToast(
                `Outcome recorded: ${this.trim(newTask.outcomes[newTask.outcomes.length - 1], 60)}`,
                "info",
              );
            } else if (newTask.attempts > oldTask.attempts) {
              this.addToast(
                `Task attempt ${newTask.attempts}: ${this.trim(newTask.description, 60)}`,
                "info",
              );
            }
          }
        }

        this.tasks = newTasks;

        // --- Update HTML Title ---
        const project = this.$$project;
        let folderName = "";
        if (project) {
          // Get the top-most folder from the project path (e.g. "a/b/c" -> "a")
          folderName = project.split("/").filter(Boolean)[0];
        } else if (this.cwd) {
          // If no project is selected, use the name of the server root folder.
          folderName = this.cwd.split("/").filter(Boolean).pop();
        }

        if (folderName) {
          document.title = `Lemming - ${folderName}`;
        } else {
          document.title = "Lemming";
        }

        // Update favicon status
        if (window.updateFavicon) {
          const hasError = newTasks.some(
            (t) => t.status === "pending" && t.attempts > 0,
          );
          const allCompleted =
            newTasks.length > 0 &&
            newTasks.every((t) => t.status === "completed");
          const state = this.loopRunning
            ? "running"
            : hasError
              ? "error"
              : allCompleted
                ? "success"
                : "idle";

          this.faviconState = state;

          // If a run starts, reset the last seen state
          if (state === "running") {
            this.lastSeenState = null;
          }

          // If the current terminal state has already been seen by the user, show 'idle' favicon instead.
          const effectiveState =
            (state === "success" || state === "error") &&
            state === this.lastSeenState
              ? "idle"
              : state;

          window.updateFavicon(effectiveState);
        }

        const contextElem = document.querySelector("textarea");
        if (
          this.loading ||
          (contextElem && document.activeElement !== contextElem)
        ) {
          this.context = data.context || "";
        }
        this.loading = false;
      };

      $.fetchRunners = async function () {
        const response = await fetch(apiUrl("/api/runners"));
        if (response.ok) {
          this.runners = await response.json();
        }
      };

      $.saveRunnerPreference = function () {
        Storage.set("selected_runner", this.selectedRunner);
      };
      $.saveRetriesPreference = function () {
        Storage.set("retries", this.retries);
      };
      $.saveHideCompletedPreference = function () {
        Storage.set("hide_completed", this.hideCompleted);
      };
      $.saveReviewPreference = function () {
        Storage.set("review_enabled", this.reviewEnabled);
      };

      let envSaveTimeout;
      $.saveEnvOverrides = function () {
        clearTimeout(envSaveTimeout);
        envSaveTimeout = setTimeout(() => {
          const toSave = this.envOverrides.map(({ key, value }) => ({
            key,
            value,
          }));
          Storage.set("env_overrides", toSave);
        }, 300);
      };

      // --- Operations ---
      $.addEnvOverride = function () {
        const id =
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : `env-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
        this.envOverrides.push({ id, key: "", value: "" });
        this.saveEnvOverrides();
      };

      $.removeEnvOverride = function (index) {
        this.envOverrides.splice(index, 1);
        this.saveEnvOverrides();
      };

      $.addTask = async function () {
        if (!this.newTask.trim()) return;
        const res = await fetch(apiUrl("/api/tasks"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: this.newTask }),
        });
        if (res.ok) {
          this.newTask = "";
          await this.fetchData();
        }
      };

      $.deleteTask = async function (id) {
        if (confirm("Delete this task?")) {
          const res = await fetch(apiUrl(`/api/tasks/${id}`), {
            method: "DELETE",
          });
          if (res.ok) await this.fetchData();
        }
      };

      $.deleteCompletedTasks = async function () {
        if (confirm("Delete ALL completed tasks?")) {
          const res = await fetch(apiUrl("/api/tasks/completed"), {
            method: "DELETE",
          });
          if (res.ok) {
            this.addToast("Completed tasks deleted", "success");
            await this.fetchData();
          }
        }
      };

      $.cancelTask = async function (id) {
        if (confirm("Cancel execution? Process will be killed.")) {
          const res = await fetch(apiUrl(`/api/tasks/${id}/cancel`), {
            method: "POST",
          });
          if (res.ok) {
            this.addToast("Execution cancelled", "info");
            await this.fetchData();
          }
        }
      };

      $.editTask = (task) => {
        $.editingTask = task;
        $.editFormData = {
          description: task.description || "",
          runner: task.runner || "",
          parent: task.parent || "",
        };
        const modal = document.getElementById("edit-modal");
        if (modal) modal.showModal();
      };

      $.closeEditModal = () => {
        const modal = document.getElementById("edit-modal");
        if (modal) modal.close();
        $.editingTask = null;
      };

      $.submitEditTask = async function () {
        if (!$.editingTask) return;

        const task = $.editingTask;
        const update = {
          description: $.editFormData.description.trim() || task.description,
          runner: $.editFormData.runner.trim() || null,
          parent: $.editFormData.parent.trim() || null,
        };

        const res = await fetch(apiUrl(`/api/tasks/${task.id}`), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(update),
        });
        if (res.ok) {
          this.addToast("Task updated", "success");
          await this.fetchData();
        }

        $.closeEditModal();
      };

      $.uncompleteTask = async function (id) {
        const res = await fetch(apiUrl(`/api/tasks/${id}`), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "pending" }),
        });
        if (res.ok) {
          this.addToast("Task reset to pending", "info");
          await this.fetchData();
        }
      };

      $.clearTask = async function (id) {
        if (confirm("Clear task attempts and outcomes?")) {
          const res = await fetch(apiUrl(`/api/tasks/${id}/clear`), {
            method: "POST",
          });
          if (res.ok) {
            this.addToast("Task cleared", "success");
            await this.fetchData();
          }
        }
      };

      $.ctxSaveTimeout = null;
      $.updateContext = function () {
        clearTimeout(this.ctxSaveTimeout);
        this.ctxSaveTimeout = setTimeout(async () => {
          const res = await fetch(apiUrl("/api/context"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ context: this.context }),
          });
          if (res.ok) this.addToast("Context saved", "info");
        }, 1000);
      };

      $.runLemming = async function () {
        const env = {};
        for (const o of this.envOverrides) {
          if (o.key?.trim()) env[o.key.trim()] = o.value || "";
        }

        const payload = {
          runner: this.selectedRunner,
          env: Object.keys(env).length > 0 ? env : undefined,
          review: this.reviewEnabled,
        };

        if (this.retries) {
          const parsed = Number.parseInt(this.retries, 10);
          if (!Number.isNaN(parsed) && parsed > 0) {
            payload.retries = parsed;
          }
        }

        const res = await fetch(apiUrl("/api/run"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          this.addToast("Run started!", "success");
          await this.fetchData();
        }
      };

      // --- Folder Picker ---
      $.openFolderPicker = async function () {
        this.folderPickerPath = "";
        await this.fetchFolderPickerDirs("");
        const modal = document.getElementById("folder-picker-modal");
        if (modal) modal.showModal();
      };

      $.closeFolderPicker = () => {
        const modal = document.getElementById("folder-picker-modal");
        if (modal) modal.close();
      };

      $.fetchFolderPickerDirs = async function (path) {
        this.folderPickerLoading = true;
        const params = path ? { path } : {};
        const res = await fetch(apiUrl("/api/directories", params));
        if (res.ok) {
          const data = await res.json();
          this.folderPickerPath = data.path;
          this.folderPickerDirs = data.directories;
        }
        this.folderPickerLoading = false;
      };

      $.folderPickerNavigate = async function (path) {
        await this.fetchFolderPickerDirs(path);
      };

      $.folderPickerUp = async function () {
        const parts = this.folderPickerPath.split("/").filter(Boolean);
        parts.pop();
        await this.fetchFolderPickerDirs(parts.join("/"));
      };

      $.folderPickerSelect = (path) => {
        // Navigate to the same page with the new project param.
        const url = new URL(window.location.href);
        if (path) {
          url.searchParams.set("project", path);
        } else {
          url.searchParams.delete("project");
        }
        window.location.href = url.toString();
      };

      $.folderPickerBreadcrumbs = $.$computed(($) => {
        const parts = $.folderPickerPath.split("/").filter(Boolean);
        const crumbs = [{ name: "root", path: "" }];
        for (let i = 0; i < parts.length; i++) {
          crumbs.push({
            name: parts[i],
            path: parts.slice(0, i + 1).join("/"),
          });
        }
        return crumbs;
      });

      // --- Final Hydration from Storage ---
      const loadedOverrides = Storage.get("env_overrides", []);
      if (loadedOverrides.length > 0) {
        $.envOverrides = loadedOverrides.map((o, i) => ({
          ...o,
          id: o.id || `env-${i}`,
        }));
      }

      // --- Mount to DOM (syncs $$project from URL) ---
      await renderer.mount(document.body);

      // --- Initial Data Fetch (after mount so $$project is available) ---
      await Promise.all([$.fetchData(), $.fetchRunners()]);

      // --- Auto-refresh via polling ---
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          const state = $.faviconState;
          if (state === "success" || state === "error") {
            $.lastSeenState = state;
            if (window.updateFavicon) window.updateFavicon("idle");
          }
        }
      });

      setInterval(() => $.fetchData(), 1000);
    },
  });
})();

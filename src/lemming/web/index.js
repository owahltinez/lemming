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
      $.config = {
        retries: 3,
        runner: "gemini",
        hooks: ["roadmap"],
      };
      $.cwd = "";
      $.newTask = "";
      $.loading = true;
      $.runners = [];
      $.availableHooks = [];
      $.selectedRunner = "gemini";
      $.retries = 3;
      $.envOverrides = []; // Will hydrate below
      $.hideCompleted = Storage.get("hide_completed", false);
      $.toasts = [];
      $.expanded = {};
      $.loopRunning = false;
      $.editingTask = null;
      $.editFormData = { description: "", parent: "" };

      // --- Favicon Status ---
      $.faviconState = "idle";
      $.lastSeenState = Storage.get("last_seen_state", null);
      // --- Folder Picker State ---
      $.folderPickerPath = "";
      $.folderPickerDirs = [];
      $.folderPickerLoading = false;
      $.showNewFolderInput = false;
      $.newFolderName = "";

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
        if (task.status === "in_progress" && task.last_started_at) {
          total += Date.now() / 1000 - task.last_started_at;
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
      $.fetchData = async () => {
        const response = await fetch(apiUrl("/api/data"));
        if (!response.ok) return;
        const data = await response.json();

        const newTasks = data.tasks || [];

        // Show toast notifications for task state changes.
        if (!$.loading && $.tasks.length > 0) {
          const oldTaskMap = new Map($.tasks.map((t) => [t.id, t]));
          for (const newTask of newTasks) {
            const oldTask = oldTaskMap.get(newTask.id);
            if (!oldTask) continue;
            if (
              oldTask.status !== "completed" &&
              newTask.status === "completed"
            ) {
              $.addToast(
                `Task completed: ${$.trim(newTask.description, 60)}`,
                "success",
              );
            } else if (
              oldTask.status === "in_progress" &&
              newTask.status === "pending"
            ) {
              $.addToast(
                `Task failed: ${$.trim(newTask.description, 60)}`,
                "error",
              );
            } else if (
              (newTask.outcomes?.length || 0) > (oldTask.outcomes?.length || 0)
            ) {
              $.addToast(
                `Outcome recorded: ${$.trim(newTask.outcomes[newTask.outcomes.length - 1], 60)}`,
                "info",
              );
            } else if (newTask.attempts > oldTask.attempts) {
              $.addToast(
                `Task attempt ${newTask.attempts}: ${$.trim(newTask.description, 60)}`,
                "info",
              );
            }
          }
        }

        // Update core state
        $.cwd = data.cwd || "";
        $.loopRunning = data.loop_running || false;
        $.tasks = newTasks;

        // Sync config from server
        if (data.config) {
          $.config = data.config;
          $.selectedRunner = data.config.runner;
          $.retries = data.config.retries;
        }

        // --- Update HTML Title ---
        const project = $.$$project;
        let folderName = "";
        if (project) {
          // Get the top-most folder from the project path (e.g. "a/b/c" -> "a")
          folderName = project.split("/").filter(Boolean)[0];
        } else if ($.cwd) {
          // If no project is selected, use the name of the server root folder.
          folderName = $.cwd.split("/").filter(Boolean).pop();
        }

        if (folderName) {
          document.title = `Lemming · ${folderName}`;
        } else {
          document.title = "Lemming";
        }

        // Update favicon status
        if (window.updateFavicon) {
          const hasError = $.tasks.some(
            (t) => t.status === "pending" && t.attempts > 0,
          );
          const allCompleted =
            $.tasks.length > 0 &&
            $.tasks.every((t) => t.status === "completed");
          const state = $.loopRunning
            ? "running"
            : hasError
              ? "error"
              : allCompleted
                ? "success"
                : "idle";

          $.faviconState = state;

          // If a run starts, reset the last seen state
          if (state === "running") {
            $.lastSeenState = null;
            Storage.set("last_seen_state", null);
          }

          // If the current terminal state has already been seen by the user, show 'idle' favicon instead.
          const effectiveState =
            (state === "success" || state === "error") &&
            state === $.lastSeenState
              ? "idle"
              : state;

          window.updateFavicon(effectiveState);
        }

        const contextElem = document.querySelector("textarea");
        if (
          $.loading ||
          (contextElem && document.activeElement !== contextElem)
        ) {
          $.context = data.context || "";
        }
        $.loading = false;
      };

      $.fetchRunners = async () => {
        const response = await fetch(apiUrl("/api/runners"));
        if (response.ok) {
          $.runners = await response.json();
        }
      };

      $.fetchHooks = async () => {
        const response = await fetch(apiUrl("/api/hooks"));
        if (response.ok) {
          $.availableHooks = await response.json();
        }
      };

      $.saveConfigToServer = async () => {
        const config = {
          retries: Number.parseInt($.retries, 10) || 3,
          runner: $.selectedRunner,
          hooks: $.config.hooks,
        };
        await fetch(apiUrl("/api/config"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(config),
        });
      };

      $.saveRunnerPreference = () => {
        $.saveConfigToServer();
      };
      $.saveRetriesPreference = () => {
        $.saveConfigToServer();
      };
      $.saveHideCompletedPreference = () => {
        Storage.set("hide_completed", $.hideCompleted);
      };
      $.toggleHook = (name) => {
        let hooks = $.config.hooks;
        if (hooks === null || hooks === undefined) {
          hooks = [...$.availableHooks];
        }
        if (hooks.includes(name)) {
          $.config.hooks = hooks.filter((h) => h !== name);
        } else {
          $.config.hooks = [...hooks, name];
        }
        $.saveConfigToServer();
      };
      $.resetHooks = () => {
        $.config.hooks = null;
        $.saveConfigToServer();
      };

      let envSaveTimeout;
      $.saveEnvOverrides = () => {
        clearTimeout(envSaveTimeout);
        envSaveTimeout = setTimeout(() => {
          const toSave = $.envOverrides.map(({ key, value }) => ({
            key,
            value,
          }));
          Storage.set("env_overrides", toSave);
        }, 300);
      };

      // --- Operations ---
      $.addEnvOverride = () => {
        const id =
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : `env-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
        $.envOverrides.push({ id, key: "", value: "" });
        $.saveEnvOverrides();
      };

      $.removeEnvOverride = (index) => {
        $.envOverrides.splice(index, 1);
        $.saveEnvOverrides();
      };

      $.addTask = async () => {
        if (!$.newTask.trim()) return;
        const res = await fetch(apiUrl("/api/tasks"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: $.newTask }),
        });
        if (res.ok) {
          $.newTask = "";
          await $.fetchData();
        }
      };

      $.deleteTask = async (id) => {
        if (confirm("Delete this task?")) {
          const res = await fetch(apiUrl(`/api/tasks/${id}/delete`), {
            method: "POST",
          });
          if (res.ok) await $.fetchData();
        }
      };

      $.deleteCompletedTasks = async () => {
        if (confirm("Delete ALL completed tasks?")) {
          const res = await fetch(apiUrl("/api/tasks/delete-completed"), {
            method: "POST",
          });
          if (res.ok) {
            $.addToast("Completed tasks deleted", "success");
            await $.fetchData();
          }
        }
      };

      $.cancelTask = async (id) => {
        if (confirm("Cancel execution? Process will be killed.")) {
          const res = await fetch(apiUrl(`/api/tasks/${id}/cancel`), {
            method: "POST",
          });
          if (res.ok) {
            $.addToast("Execution cancelled", "info");
            await $.fetchData();
          }
        }
      };

      $.editTask = (task) => {
        $.editingTask = task;
        $.editFormData = {
          description: task.description || "",
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

      $.submitEditTask = async () => {
        if (!$.editingTask) return;

        const task = $.editingTask;
        const update = {
          description: $.editFormData.description.trim() || task.description,
          parent: $.editFormData.parent.trim() || null,
        };

        const res = await fetch(apiUrl(`/api/tasks/${task.id}/update`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(update),
        });
        if (res.ok) {
          $.addToast("Task updated", "success");
          await $.fetchData();
        }

        $.closeEditModal();
      };

      $.uncompleteTask = async (id) => {
        const res = await fetch(apiUrl(`/api/tasks/${id}/update`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "pending" }),
        });
        if (res.ok) {
          $.addToast("Task reset to pending", "info");
          await $.fetchData();
        }
      };

      $.clearTask = async (id) => {
        if (confirm("Clear task attempts and outcomes?")) {
          const res = await fetch(apiUrl(`/api/tasks/${id}/clear`), {
            method: "POST",
          });
          if (res.ok) {
            $.addToast("Task cleared", "success");
            await $.fetchData();
          }
        }
      };

      $.ctxSaveTimeout = null;
      $.updateContext = () => {
        clearTimeout($.ctxSaveTimeout);
        $.ctxSaveTimeout = setTimeout(async () => {
          const res = await fetch(apiUrl("/api/context"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ context: $.context }),
          });
          if (res.ok) $.addToast("Context saved", "info");
        }, 1000);
      };

      $.runLemming = async () => {
        const env = {};
        for (const o of $.envOverrides) {
          if (o.key?.trim()) env[o.key.trim()] = o.value || "";
        }

        const payload = {
          env: Object.keys(env).length > 0 ? env : undefined,
        };

        const res = await fetch(apiUrl("/api/run"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          $.addToast("Run started!", "success");
          await $.fetchData();
        }
      };

      // --- Folder Picker ---
      $.openFolderPicker = async () => {
        $.folderPickerPath = "";
        $.showNewFolderInput = false;
        $.newFolderName = "";
        await $.fetchFolderPickerDirs("");
        const modal = document.getElementById("folder-picker-modal");
        if (modal) modal.showModal();
      };

      $.closeFolderPicker = () => {
        const modal = document.getElementById("folder-picker-modal");
        if (modal) modal.close();
      };

      $.startNewFolder = () => {
        $.showNewFolderInput = true;
        $.newFolderName = "";
      };

      $.fetchFolderPickerDirs = async (path) => {
        $.folderPickerLoading = true;
        const params = new URLSearchParams(path ? { path } : {});
        const res = await fetch(`/api/directories?${params.toString()}`);
        if (res.ok) {
          const data = await res.json();
          $.folderPickerPath = data.path;
          $.folderPickerDirs = data.directories;
        }
        $.folderPickerLoading = false;
      };

      $.createFolder = async () => {
        if (!$.newFolderName) return;
        const res = await fetch("/api/directories", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: $.folderPickerPath,
            name: $.newFolderName,
          }),
        });
        if (res.ok) {
          $.addToast("Folder created!", "success");
          $.showNewFolderInput = false;
          $.newFolderName = "";
          await $.fetchFolderPickerDirs($.folderPickerPath);
        } else {
          const err = await res.json();
          $.addToast(err.detail || "Failed to create folder", "error");
        }
      };
      $.folderPickerNavigate = async (path) => {
        await $.fetchFolderPickerDirs(path);
      };

      $.folderPickerUp = async () => {
        const parts = $.folderPickerPath.split("/").filter(Boolean);
        parts.pop();
        await $.fetchFolderPickerDirs(parts.join("/"));
      };

      $.folderPickerSelect = (path) => {
        // Navigate to the same page with the new project param.
        const url = new URL(window.location.href);
        if (path) {
          url.searchParams.set("project", path);
        } else {
          url.searchParams.delete("project");
        }
        window.open(url.toString(), "_blank");
        $.closeFolderPicker();
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
      await Promise.all([$.fetchData(), $.fetchRunners(), $.fetchHooks()]);

      // --- Auto-refresh via polling ---
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          // Force an immediate fetch when returning to the tab.
          $.fetchData();

          const state = $.faviconState;
          if (state === "success" || state === "error") {
            $.lastSeenState = state;
            Storage.set("last_seen_state", state);
            if (window.updateFavicon) window.updateFavicon("idle");
          }
        }
      });

      setInterval(() => $.fetchData(), 1000);
    },
  });
})();

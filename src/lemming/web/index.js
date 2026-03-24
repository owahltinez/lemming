(async () => {
  // Mancha.initMancha is the robust way to initialize.
  window.ManchaApp = Mancha.initMancha({
    cloak: true,
    callback: async (renderer) => {
      const { $ } = renderer;

      // --- Persistence Management ---
      const Storage = {
        get(key, fallback) {
          try {
            const val = localStorage.getItem(key);
            return val !== null ? JSON.parse(val) : fallback;
          } catch {
            return fallback;
          }
        },
        set(key, val) {
          localStorage.setItem(key, JSON.stringify(val));
        },
      };

      // --- Initial State ---
      $.tasks = [];
      $.context = "";
      $.cwd = "";
      $.newTask = "";
      $.loading = true;
      $.agents = [];
      $.selectedAgent = Storage.get("lemming_selected_agent", "gemini");
      $.maxAttempts = Storage.get("lemming_max_attempts", 3);
      $.envOverrides = []; // Will hydrate below
      $.hideCompleted = Storage.get("lemming_hide_completed", false);
      $.toasts = [];
      $.expanded = {};
      $.loopRunning = false;
      $.editingTask = null;
      $.editFormData = { description: "", agent: "", parent: "" };

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
        if (!seconds) return "0.0s";
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
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
        const response = await fetch("/api/data");
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
        const contextElem = document.querySelector("textarea");
        if (
          this.loading ||
          (contextElem && document.activeElement !== contextElem)
        ) {
          this.context = data.context || "";
        }
        this.loading = false;
      };

      $.fetchAgents = async function () {
        const response = await fetch("/api/agents");
        if (response.ok) {
          this.agents = await response.json();
        }
      };

      $.saveAgentPreference = function () {
        Storage.set("lemming_selected_agent", this.selectedAgent);
      };
      $.saveMaxAttemptsPreference = function () {
        Storage.set("lemming_max_attempts", this.maxAttempts);
      };
      $.saveHideCompletedPreference = function () {
        Storage.set("lemming_hide_completed", this.hideCompleted);
      };

      let envSaveTimeout;
      $.saveEnvOverrides = function () {
        clearTimeout(envSaveTimeout);
        envSaveTimeout = setTimeout(() => {
          const toSave = this.envOverrides.map(({ key, value }) => ({
            key,
            value,
          }));
          Storage.set("lemming_env_overrides", toSave);
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
        const res = await fetch("/api/tasks", {
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
          const res = await fetch(`/api/tasks/${id}`, { method: "DELETE" });
          if (res.ok) await this.fetchData();
        }
      };

      $.deleteCompletedTasks = async function () {
        if (confirm("Delete ALL completed tasks?")) {
          const res = await fetch("/api/tasks/completed", { method: "DELETE" });
          if (res.ok) {
            this.addToast("Completed tasks deleted", "success");
            await this.fetchData();
          }
        }
      };

      $.cancelTask = async function (id) {
        if (confirm("Cancel execution? Process will be killed.")) {
          const res = await fetch(`/api/tasks/${id}/cancel`, {
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
          agent: task.agent || "",
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
          agent: $.editFormData.agent.trim() || null,
          parent: $.editFormData.parent.trim() || null,
        };

        const res = await fetch(`/api/tasks/${task.id}`, {
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
        const res = await fetch(`/api/tasks/${id}`, {
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
          const res = await fetch(`/api/tasks/${id}/clear`, { method: "POST" });
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
          const res = await fetch("/api/context", {
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
          agent: this.selectedAgent,
          env: Object.keys(env).length > 0 ? env : undefined,
        };

        if (this.maxAttempts) {
          const parsed = Number.parseInt(this.maxAttempts, 10);
          if (!Number.isNaN(parsed) && parsed > 0) {
            payload.max_attempts = parsed;
          }
        }

        const res = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          this.addToast("Run started!", "success");
          await this.fetchData();
        }
      };

      // --- Final Hydration from Storage ---
      const loadedOverrides = Storage.get("lemming_env_overrides", []);
      if (loadedOverrides.length > 0) {
        $.envOverrides = loadedOverrides.map((o, i) => ({
          ...o,
          id: o.id || `env-${i}`,
        }));
      }

      // --- Initial Data Fetch ---
      await Promise.all([$.fetchData(), $.fetchAgents()]);

      // --- Mount to DOM ---
      await renderer.mount(document.body);

      // --- Auto-refresh via polling ---
      setInterval(() => $.fetchData(), 2000);
    },
  });
})();

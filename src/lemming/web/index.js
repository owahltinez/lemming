(async () => {
  // Mancha.initMancha is the robust way to initialize.
  window.ManchaApp = Mancha.initMancha({
    cloak: true,
    callback: async (renderer) => {
      const { $ } = renderer;

      // --- Project Scoping ---
      // $$project is auto-synced with the ?project= URL query param by mancha.
      $.$$project = $.$$project ?? '';

      // Helper: build a URL with the project query param.
      function apiUrl(path, extraParams = {}) {
        const params = new URLSearchParams(extraParams);
        if ($.$$project) params.set('project', $.$$project);
        const qs = params.toString();
        return qs ? `${path}?${qs}` : path;
      }

      // --- Persistence Management (scoped by project) ---
      const Storage = {
        getPrefix() {
          return `lemming[${$.$$project}]_`;
        },
        get(key, fallback) {
          try {
            const val = localStorage.getItem(this.getPrefix() + key);
            return val !== null ? JSON.parse(val) : fallback;
          } catch {
            return fallback;
          }
        },
        set(key, val) {
          localStorage.setItem(this.getPrefix() + key, JSON.stringify(val));
        },
      };

      // --- Initial State ---
      $.tasks = [];
      $.goal = '';
      $.config = {
        retries: 3,
        runner: 'agy',
      };
      $.cwd = '';
      $.newTask = '';
      $.loading = true;
      $.runners = [];
      $.availableHooks = [];
      $.selectedRunner = 'agy';
      $.retries = 3;
      $.timeLimit = '60';
      $.envOverrides = []; // Will hydrate below
      $.hideCompleted = false; // Hydrate after mount
      $.toasts = [];
      $.expanded = {};
      $.loopRunning = false;
      $.editingTask = null;
      $.editFormData = { description: '', parent: '' };

      // --- Favicon Status ---
      $.faviconState = 'idle';
      $.lastSeenState = null; // Hydrate after mount
      // --- Folder Picker State ---
      $.folderPickerPath = '';
      $.folderPickerDirs = [];
      $.folderPickerLoading = false;
      $.showNewFolderInput = false;
      $.newFolderName = '';

      $.isHistoryTask = (task) =>
        ['completed', 'failed', 'cancelled', 'superseded'].includes(
          task.status,
        );
      $.getTaskStatusLabel = (task) => {
        if (task.status === 'completed') return 'Completed';
        if (task.status === 'failed') return 'Failed';
        if (task.status === 'cancelled') return 'Cancelled';
        if (task.status === 'superseded') return 'Superseded';
        if (task.status === 'in_progress') {
          return task.requested_status ? 'Finalizing' : 'Running';
        }
        return task.attempts > 0 ? 'Retrying' : 'Pending';
      };
      $.getTaskStatusClass = (task) => {
        if (task.status === 'completed') return 'bg-green-100 text-green-700';
        if (task.status === 'failed') return 'bg-red-100 text-red-700';
        if (task.status === 'cancelled') return 'bg-orange-100 text-orange-700';
        if (task.status === 'superseded') {
          return 'bg-purple-100 text-purple-700';
        }
        if (task.status === 'in_progress') {
          return 'bg-blue-100 text-blue-700 animate-pulse';
        }
        return task.attempts > 0
          ? 'bg-amber-100 text-amber-700'
          : 'bg-gray-100 text-gray-500';
      };
      $.getTaskTextClass = (task) => {
        if (task.status === 'completed') return 'text-gray-400 line-through';
        if (task.status === 'cancelled') return 'text-orange-600 line-through';
        if (task.status === 'superseded') return 'text-purple-600';
        if (task.status === 'failed') return 'text-red-600';
        if (task.status === 'in_progress') return 'text-blue-600';
        return task.attempts > 0 ? 'text-amber-600' : '';
      };

      // --- Computed Properties ---
      $.runningCount = $.$computed(
        ($) => $.tasks.filter((t) => t.status === 'in_progress').length,
      );
      $.pendingCount = $.$computed(
        ($) => $.tasks.filter((t) => t.status === 'pending').length,
      );
      $.completedCount = $.$computed(
        ($) => $.tasks.filter((t) => t.status === 'completed').length,
      );
      $.failedCount = $.$computed(
        ($) =>
          $.tasks.filter(
            (t) => t.status === 'failed' || t.status === 'cancelled',
          ).length,
      );
      $.supersededCount = $.$computed(
        ($) => $.tasks.filter((t) => t.status === 'superseded').length,
      );
      $.historyCount = $.$computed(
        ($) => $.tasks.filter((t) => $.isHistoryTask(t)).length,
      );

      $.filteredTasks = $.$computed(($) => {
        const ts = [...$.tasks];
        // Sort in frontend to ensure consistent order:
        // 1. Uncompleted tasks (pending, in_progress) first.
        // 2. Completed tasks (completed, failed) at the bottom.
        ts.sort((a, b) => {
          const aDone = $.isHistoryTask(a) ? 1 : 0;
          const bDone = $.isHistoryTask(b) ? 1 : 0;
          if (aDone !== bDone) return aDone - bDone;

          if (!aDone) {
            // Uncompleted tasks: prioritize in_progress first, then FIFO (index)
            const aInProgress = a.status === 'in_progress' ? 0 : 1;
            const bInProgress = b.status === 'in_progress' ? 0 : 1;
            if (aInProgress !== bInProgress) return aInProgress - bInProgress;

            if (a.index !== b.index) return (a.index || 0) - (b.index || 0);
            return (a.created_at || 0) - (b.created_at || 0);
          }

          // Completed tasks: newest first (reverse chronological by completion/creation time).
          const aTime = a.completed_at || a.superseded_at || a.created_at || 0;
          const bTime = b.completed_at || b.superseded_at || b.created_at || 0;
          if (aTime !== bTime) return bTime - aTime;

          return (b.index || 0) - (a.index || 0);
        });
        return ts.filter((t) => !$.isHistoryTask(t) || !$.hideCompleted);
      });

      // --- Utilities ---
      $.trim = (s, l = 60) =>
        s && s.length > l ? `${s.substring(0, l - 3)}...` : s;
      $.formatDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : '');
      $.formatDuration = (seconds) => {
        if (!seconds) return '0s';
        if (seconds < 60) return `${Math.floor(seconds)}s`;
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = Math.floor(seconds % 60);
        return `${minutes}m ${remainingSeconds}s`;
      };
      $.formatTaskRunTime = (task) => {
        let total = task.run_time || 0;
        if (task.status === 'in_progress' && task.last_started_at) {
          total += Date.now() / 1000 - task.last_started_at;
        }
        return $.formatDuration(total);
      };
      $.getExecutionSegments = (task) => {
        const hookColors = [
          '#059669',
          '#d97706',
          '#db2777',
          '#0891b2',
          '#7c3aed',
          '#dc2626',
        ];
        const hookColorClasses = [
          'bg-green-600',
          'bg-amber-600',
          'bg-pink-600',
          'bg-cyan-600',
          'bg-purple-600',
          'bg-red-600',
        ];
        const entries = Object.entries(task.execution_times || {}).filter(
          ([, duration]) => Number.isFinite(Number(duration)) && duration > 0,
        );
        entries.sort(([a], [b]) => {
          if (a === 'runner') return -1;
          if (b === 'runner') return 1;
          return 0;
        });
        const total = entries.reduce(
          (sum, [, duration]) => sum + Number(duration),
          0,
        );
        const usedHookColors = new Set();

        return entries.map(([key, duration], index) => {
          const label = key === 'runner' ? 'Runner' : key.replace(/^hook:/, '');
          let hash = 0;
          for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) | 0;
          let color = '#4f46e5';
          let colorClass = 'bg-indigo-600';
          if (key !== 'runner') {
            let colorIndex = Math.abs(hash || index) % hookColors.length;
            while (
              usedHookColors.has(colorIndex) &&
              usedHookColors.size < hookColors.length
            ) {
              colorIndex = (colorIndex + 1) % hookColors.length;
            }
            usedHookColors.add(colorIndex);
            color = hookColors[colorIndex];
            colorClass = hookColorClasses[colorIndex];
          }
          return {
            key,
            label,
            duration: Number(duration),
            percent: (Number(duration) / total) * 100,
            color,
            colorClass,
          };
        });
      };
      $.getExecutionSummary = (task) =>
        $.getExecutionSegments(task)
          .map(
            (segment) =>
              `${segment.label} ${$.formatDuration(segment.duration)}`,
          )
          .join(', ');

      $.getParent = (parentId) => {
        return $.tasks.find((t) => t.id === parentId);
      };
      $.getChildren = (parentId) => {
        return $.tasks.filter((t) => t.parent === parentId);
      };
      $.focusTask = (taskId) => {
        $.expanded[taskId] = true;
        requestAnimationFrame(() => {
          document
            .querySelector(`[data-task-id="${CSS.escape(taskId)}"]`)
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
      };

      $.copyToClipboard = function (text) {
        if (!navigator.clipboard) {
          const el = document.createElement('textarea');
          el.value = text;
          document.body.appendChild(el);
          el.select();
          document.execCommand('copy');
          document.body.removeChild(el);
        } else {
          navigator.clipboard.writeText(text);
        }
        this.addToast('Copied to clipboard', 'info');
      };

      // --- UI Feedback ---
      $.addToast = function (message, type = 'info') {
        const id = Date.now() + Math.random();
        this.toasts.push({ id, message: this.trim(message, 120), type });
        setTimeout(() => {
          this.toasts = this.toasts.filter((t) => t.id !== id);
        }, 5000);
      };

      // --- Data Actions ---
      $.notifyChanges = (oldTasks, newTasks) => {
        if ($.loading || !oldTasks.length) return;
        const oldTaskMap = new Map(oldTasks.map((t) => [t.id, t]));

        for (const newTask of newTasks) {
          const oldTask = oldTaskMap.get(newTask.id);
          if (!oldTask) continue;

          if (
            oldTask.status !== 'completed' &&
            newTask.status === 'completed'
          ) {
            $.addToast(
              `Task completed: ${$.trim(newTask.description, 60)}`,
              'success',
            );
          } else if (
            oldTask.status !== 'failed' &&
            newTask.status === 'failed'
          ) {
            $.addToast(
              `Terminal failure: ${$.trim(newTask.description, 60)}`,
              'error',
            );
          } else if (
            oldTask.status !== 'cancelled' &&
            newTask.status === 'cancelled'
          ) {
            $.addToast(
              `Task cancelled: ${$.trim(newTask.description, 60)}`,
              'info',
            );
          } else if (
            oldTask.status !== 'superseded' &&
            newTask.status === 'superseded'
          ) {
            $.addToast(
              `Task superseded: ${$.trim(newTask.description, 60)}`,
              'info',
            );
          } else if (
            oldTask.status === 'in_progress' &&
            newTask.status === 'pending'
          ) {
            $.addToast(
              `Attempt failed (retry pending): ${$.trim(newTask.description, 60)}`,
              'error',
            );
          } else if (
            (newTask.progress?.length || 0) > (oldTask.progress?.length || 0)
          ) {
            $.addToast(
              `Progress recorded: ${$.trim(newTask.progress[newTask.progress.length - 1], 60)}`,
              'info',
            );
          } else if (newTask.attempts > oldTask.attempts) {
            $.addToast(
              `Task attempt ${newTask.attempts}: ${$.trim(newTask.description, 60)}`,
              'info',
            );
          }
        }
      };

      $.updateTitle = () => {
        const project = $.$$project;
        let folderName = '';
        if (project) {
          folderName = project.split('/').filter(Boolean)[0];
        } else if ($.cwd) {
          folderName = $.cwd.split('/').filter(Boolean).pop();
        }
        document.title = folderName ? `Lemming · ${folderName}` : 'Lemming';
      };

      $.updateFaviconStatus = () => {
        if (!window.updateFavicon) return;
        const hasError = $.tasks.some(
          (t) =>
            (t.status === 'pending' && t.attempts > 0) ||
            t.status === 'cancelled',
        );
        const allCompleted =
          $.tasks.length > 0 &&
          $.tasks.every(
            (t) => t.status === 'completed' || t.status === 'superseded',
          );
        const state = $.loopRunning
          ? 'running'
          : hasError
            ? 'error'
            : allCompleted
              ? 'success'
              : 'idle';

        $.faviconState = state;
        if (state === 'running') {
          $.lastSeenState = null;
          Storage.set('last_seen_state', null);
        }

        const effectiveState =
          (state === 'success' || state === 'error') &&
          state === $.lastSeenState
            ? 'idle'
            : state;
        window.updateFavicon(effectiveState);
      };

      $.fetchData = async () => {
        const response = await fetch(apiUrl('/api/data'));
        if (!response.ok) return;
        const data = await response.json();
        const newTasks = data.tasks || [];

        // Guard against transient empty responses from the server
        // (e.g. reading the YAML file mid-write). Skip the update entirely
        // so the UI doesn't flash empty and fire rogue notifications.
        if (!newTasks.length && $.tasks.length) return;

        $.notifyChanges($.tasks, newTasks);

        // Update core state
        $.cwd = data.cwd || '';
        $.loopRunning = data.loop_running || false;
        $.tasks = newTasks;

        // Sync config from server
        if (data.config) {
          $.config = data.config;

          const runnerElem = document.querySelector(
            'input[aria-label="Runner command"]',
          );
          if (
            $.loading ||
            !runnerElem ||
            document.activeElement !== runnerElem
          ) {
            $.selectedRunner = data.config.runner;
          }

          const retriesElem = document.querySelector(
            'input[aria-label="Retries"]',
          );
          if (
            $.loading ||
            !retriesElem ||
            document.activeElement !== retriesElem
          ) {
            $.retries = data.config.retries;
          }

          const timeLimitElem = document.querySelector(
            'select[aria-label="Time limit"]',
          );
          if (
            $.loading ||
            !timeLimitElem ||
            document.activeElement !== timeLimitElem
          ) {
            $.timeLimit = String(data.config.time_limit || 0);
          }
        }

        $.updateTitle();
        $.updateFaviconStatus();

        const goalElem = document.querySelector('textarea');
        if ($.loading || (goalElem && document.activeElement !== goalElem)) {
          $.goal = data.goal || '';
        }
        $.loading = false;
      };

      $.fetchRunners = async () => {
        const response = await fetch(apiUrl('/api/runners'));
        if (response.ok) {
          $.runners = await response.json();
        }
      };

      $.fetchHooks = async () => {
        const response = await fetch(apiUrl('/api/hooks'));
        if (response.ok) {
          $.availableHooks = await response.json();
        }
      };

      $.saveConfigToServer = async () => {
        const config = {
          retries: Number.parseInt($.retries, 10) || 3,
          runner: $.selectedRunner,
          time_limit: Number.parseInt($.timeLimit, 10) || 0,
        };
        await fetch(apiUrl('/api/config'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config),
        });
      };

      $.saveRunnerPreference = () => {
        $.saveConfigToServer();
      };
      $.saveRetriesPreference = () => {
        $.saveConfigToServer();
      };
      $.saveTimeLimitPreference = () => {
        $.saveConfigToServer();
      };
      $.saveHideCompletedPreference = () => {
        Storage.set('hide_completed', $.hideCompleted);
      };
      $.toggleHook = async (hook) => {
        // Enabling removes the project mask file; disabling creates it
        const response = await fetch(apiUrl('/api/hooks'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: hook.name, enabled: hook.masked }),
        });
        if (response.ok) {
          $.availableHooks = await response.json();
        } else {
          const error = await response.json().catch(() => ({}));
          $.addToast(error.detail || 'Failed to toggle hook', 'error');
          // Re-fetch so the checkbox reverts to the server state
          await $.fetchHooks();
        }
      };

      let envSaveTimeout;
      $.saveEnvOverrides = () => {
        clearTimeout(envSaveTimeout);
        envSaveTimeout = setTimeout(() => {
          const toSave = $.envOverrides.map(({ key, value }) => ({
            key,
            value,
          }));
          Storage.set('env_overrides', toSave);
        }, 300);
      };

      // --- Operations ---
      $.addEnvOverride = () => {
        const id =
          typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : `env-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
        $.envOverrides.push({ id, key: '', value: '' });
        $.saveEnvOverrides();
      };

      $.removeEnvOverride = (index) => {
        $.envOverrides.splice(index, 1);
        $.saveEnvOverrides();
      };

      $.addTask = async () => {
        if (!$.newTask.trim()) return;
        const res = await fetch(apiUrl('/api/tasks'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: $.newTask }),
        });
        if (res.ok) {
          $.newTask = '';
          await $.fetchData();
        }
      };

      $.taskActionTarget = null;
      $.taskActionTargetStatus = null;
      $.taskActionTargetHasAttempts = false;

      $.openTaskActionModal = (id) => {
        $.taskActionTarget = id;
        const task = $.tasks.find((t) => t.id === id);
        $.taskActionTargetStatus = task ? task.status : null;
        $.taskActionTargetHasAttempts = task ? task.attempts > 0 : false;
        const modal = document.getElementById('task-action-modal');
        if (modal) modal.showModal();
      };

      $.closeTaskActionModal = () => {
        const modal = document.getElementById('task-action-modal');
        if (modal) modal.close();
        $.taskActionTarget = null;
        $.taskActionTargetStatus = null;
        $.taskActionTargetHasAttempts = false;
      };

      $.editTaskFromModal = () => {
        const task = $.tasks.find((t) => t.id === $.taskActionTarget);
        $.closeTaskActionModal();
        if (task) $.editTask(task);
      };

      $.clearTaskFromModal = () => {
        const id = $.taskActionTarget;
        $.closeTaskActionModal();
        if (id) $.clearTask(id);
      };

      $.deleteTask = async (id) => {
        const res = await fetch(apiUrl(`/api/tasks/${id}/delete`), {
          method: 'POST',
        });
        if (res.ok) await $.fetchData();
      };

      $.confirmDeleteTask = async () => {
        if (!$.taskActionTarget) return;
        await $.deleteTask($.taskActionTarget);
        $.closeTaskActionModal();
      };

      $.confirmCancelTask = async () => {
        if (!$.taskActionTarget) return;
        const id = $.taskActionTarget;
        const res = await fetch(apiUrl(`/api/tasks/${id}/cancel`), {
          method: 'POST',
        });
        if (res.ok) {
          $.addToast('Execution cancelled', 'info');
          await $.fetchData();
        }
        $.closeTaskActionModal();
      };

      $.confirmReopenTask = async () => {
        if (!$.taskActionTarget) return;
        const id = $.taskActionTarget;
        const res = await fetch(apiUrl(`/api/tasks/${id}/update`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'pending' }),
        });
        if (res.ok) {
          $.addToast('Task re-opened', 'info');
          await $.fetchData();
        }
        $.closeTaskActionModal();
      };

      $.deleteCompletedTasks = async () => {
        if (confirm('Permanently delete ALL task history and its logs?')) {
          const res = await fetch(apiUrl('/api/tasks/delete-completed'), {
            method: 'POST',
          });
          if (res.ok) {
            $.addToast('Task history deleted', 'success');
            await $.fetchData();
          }
        }
      };

      $.cancelTask = async (id) => {
        $.openTaskActionModal(id);
      };

      $.editTask = (task) => {
        $.editingTask = task;
        $.editFormData = {
          description: task.description || '',
          parent: task.parent || '',
        };
        const modal = document.getElementById('edit-modal');
        if (modal) modal.showModal();
      };

      $.closeEditModal = () => {
        const modal = document.getElementById('edit-modal');
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
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(update),
        });
        if (res.ok) {
          $.addToast('Task updated', 'success');
          await $.fetchData();
        }

        $.closeEditModal();
      };

      $.uncompleteTask = async (id) => {
        const res = await fetch(apiUrl(`/api/tasks/${id}/update`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'pending' }),
        });
        if (res.ok) {
          $.addToast('Task reset to pending', 'info');
          await $.fetchData();
        }
      };

      $.clearTask = async (id) => {
        if (confirm('Clear task attempts and progress?')) {
          const res = await fetch(apiUrl(`/api/tasks/${id}/clear`), {
            method: 'POST',
          });
          if (res.ok) {
            $.addToast('Task cleared', 'success');
            await $.fetchData();
          }
        }
      };

      $.goalSaveTimeout = null;
      $.updateGoal = () => {
        clearTimeout($.goalSaveTimeout);
        $.goalSaveTimeout = setTimeout(async () => {
          const res = await fetch(apiUrl('/api/goal'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ goal: $.goal }),
          });
          if (res.ok) $.addToast('Goal saved', 'info');
        }, 1000);
      };

      $.runLemming = async () => {
        const env = {};
        for (const o of $.envOverrides) {
          if (o.key?.trim()) env[o.key.trim()] = o.value || '';
        }

        const payload = {
          env: Object.keys(env).length > 0 ? env : undefined,
        };

        const res = await fetch(apiUrl('/api/run'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          $.addToast('Run started!', 'success');
          await $.fetchData();
        }
      };

      // --- Folder Picker ---
      $.openFolderPicker = async () => {
        $.folderPickerPath = '';
        $.showNewFolderInput = false;
        $.newFolderName = '';
        await $.fetchFolderPickerDirs('');
        const modal = document.getElementById('folder-picker-modal');
        if (modal) modal.showModal();
      };

      $.closeFolderPicker = () => {
        const modal = document.getElementById('folder-picker-modal');
        if (modal) modal.close();
      };

      $.startNewFolder = () => {
        $.showNewFolderInput = true;
        $.newFolderName = '';
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
        const res = await fetch('/api/directories', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: $.folderPickerPath,
            name: $.newFolderName,
          }),
        });
        if (res.ok) {
          $.addToast('Folder created!', 'success');
          $.showNewFolderInput = false;
          $.newFolderName = '';
          await $.fetchFolderPickerDirs($.folderPickerPath);
        } else {
          const err = await res.json();
          $.addToast(err.detail || 'Failed to create folder', 'error');
        }
      };
      $.folderPickerNavigate = async (path) => {
        await $.fetchFolderPickerDirs(path);
      };

      $.folderPickerUp = async () => {
        const parts = $.folderPickerPath.split('/').filter(Boolean);
        parts.pop();
        await $.fetchFolderPickerDirs(parts.join('/'));
      };

      $.folderPickerSelect = (path) => {
        // Navigate to the same page with the new project param.
        const url = new URL(window.location.href);
        if (path) {
          url.searchParams.set('project', path);
        } else {
          url.searchParams.delete('project');
        }
        window.open(url.toString(), '_blank');
        $.closeFolderPicker();
      };

      $.folderPickerBreadcrumbs = $.$computed(($) => {
        const parts = $.folderPickerPath.split('/').filter(Boolean);
        const crumbs = [{ name: 'root', path: '' }];
        for (let i = 0; i < parts.length; i++) {
          crumbs.push({
            name: parts[i],
            path: parts.slice(0, i + 1).join('/'),
          });
        }
        return crumbs;
      });

      // --- Mount to DOM (syncs $$project from URL) ---
      await renderer.mount(document.body);

      // --- Final Hydration from Storage (Post-Mount) ---
      $.hideCompleted = Storage.get('hide_completed', false);
      $.lastSeenState = Storage.get('last_seen_state', null);
      const loadedOverrides = Storage.get('env_overrides', []);
      if (loadedOverrides.length > 0) {
        $.envOverrides = loadedOverrides.map((o, i) => ({
          ...o,
          id: o.id || `env-${i}`,
        }));
      }

      // --- Initial Data Fetch (after mount so $$project is available) ---
      await Promise.all([$.fetchData(), $.fetchRunners(), $.fetchHooks()]);

      // --- Auto-refresh via polling ---
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
          // Force an immediate fetch when returning to the tab.
          $.fetchData();
          $.fetchHooks();

          const state = $.faviconState;
          if (state === 'success' || state === 'error') {
            $.lastSeenState = state;
            Storage.set('last_seen_state', state);
            if (window.updateFavicon) window.updateFavicon('idle');
          }
        }
      });

      setInterval(() => $.fetchData(), 1000);
      // Hooks change rarely (CLI toggles, other tabs); poll them slowly
      setInterval(() => $.fetchHooks(), 5000);
    },
  });
})();

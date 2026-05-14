import React, { useState, useEffect } from 'react';
import { Plus, Trash2, CheckCircle, Clock, AlertCircle, XCircle } from 'lucide-react';
import api from '../../api/client';

const AgentTodoList = () => {
  const [todos, setTodos] = useState([]);
  const [aliasReviews, setAliasReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reviewLoading, setReviewLoading] = useState(true);
  const [error, setError] = useState(null);
  const [aliasError, setAliasError] = useState(null);
  const [reviewActionId, setReviewActionId] = useState(null);
  const [reviewFilter, setReviewFilter] = useState('pending');
  const [filters, setFilters] = useState({ status: null, agent: null, category: null });
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [formData, setFormData] = useState({
    title: '', description: '', priority: 'medium', assigned_to_agent: 'rex', category: 'general'
  });

  useEffect(() => {
    fetchTodos();
    const interval = setInterval(fetchTodos, 5000);
    return () => clearInterval(interval);
  }, [filters]);

  useEffect(() => {
    fetchAliasReviews();
    const interval = setInterval(fetchAliasReviews, 7000);
    return () => clearInterval(interval);
  }, [reviewFilter]);

  const fetchTodos = async () => {
    try {
      const params = {};
      if (filters.status) params.status = filters.status;
      if (filters.agent) params.agent = filters.agent;
      if (filters.category) params.category = filters.category;
      const { data } = await api.get('/admin/todos', { params });
      setTodos(Array.isArray(data) ? data : []);
      setError(null);
    } catch (err) {
      const code = err?.response?.status;
      setError(code ? `HTTP ${code}` : (err?.message || 'Request failed'));
    } finally {
      setLoading(false);
    }
  };

  const fetchAliasReviews = async () => {
    try {
      const params = { limit: 150 };
      if (reviewFilter) params.status = reviewFilter;
      const { data } = await api.get('/admin/alias-reviews', { params });
      setAliasReviews(Array.isArray(data) ? data : []);
      setAliasError(null);
    } catch (err) {
      const code = err?.response?.status;
      setAliasError(code ? `HTTP ${code}` : (err?.message || 'Request failed'));
    } finally {
      setReviewLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!formData.title.trim()) {
      alert('Title required');
      return;
    }
    try {
      await api.post('/admin/todos', formData);
      setFormData({
        title: '', description: '', priority: 'medium', assigned_to_agent: 'rex', category: 'general'
      });
      setShowCreateForm(false);
      await fetchTodos();
    } catch (err) {
      const code = err?.response?.status;
      alert(`Create failed: ${code ? `HTTP ${code}` : (err?.message || 'Request failed')}`);
    }
  };

  const handleUpdate = async (todoId, updates) => {
    try {
      await api.put(`/admin/todos/${todoId}`, updates);
      await fetchTodos();
    } catch (err) {
      const code = err?.response?.status;
      alert(`Update failed: ${code ? `HTTP ${code}` : (err?.message || 'Request failed')}`);
    }
  };

  const handleDelete = async (todoId) => {
    if (!confirm('Delete this todo?')) return;
    try {
      await api.delete(`/admin/todos/${todoId}`);
      await fetchTodos();
    } catch (err) {
      const code = err?.response?.status;
      alert(`Delete failed: ${code ? `HTTP ${code}` : (err?.message || 'Request failed')}`);
    }
  };

  const handleAliasDecision = async (reviewId, decision) => {
    setReviewActionId(reviewId);
    try {
      await api.post(`/admin/alias-reviews/${reviewId}/${decision}`);
      await fetchAliasReviews();
    } catch (err) {
      const code = err?.response?.status;
      alert(`${decision} failed: ${code ? `HTTP ${code}` : (err?.message || 'Request failed')}`);
    } finally {
      setReviewActionId(null);
    }
  };

  const statusIcon = (status) => ({
    completed: <CheckCircle className="w-4 h-4 text-green-600" />,
    in_progress: <Clock className="w-4 h-4 text-blue-600" />,
    blocked: <AlertCircle className="w-4 h-4 text-red-600" />,
  }[status] || <Clock className="w-4 h-4 text-gray-400" />);

  const statusColor = (status) => ({
    completed: 'bg-green-100 text-green-800',
    in_progress: 'bg-blue-100 text-blue-800',
    blocked: 'bg-red-100 text-red-800',
    cancelled: 'bg-gray-100 text-gray-800',
  }[status] || 'bg-yellow-100 text-yellow-800');

  const priorityColor = (priority) => ({
    critical: 'text-red-600 font-bold',
    high: 'text-orange-600',
    medium: 'text-blue-600',
  }[priority] || 'text-gray-600');

  if (loading && todos.length === 0) return <div className="p-4 text-center text-gray-500">Loading...</div>;

  return (
    <div className="space-y-4 p-6 bg-gray-50">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold text-gray-900">Agent Task List</h2>
        <button
          onClick={() => setShowCreateForm(!showCreateForm)}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg"
        >
          <Plus className="w-4 h-4" /> New Task
        </button>
      </div>

      {showCreateForm && (
        <div className="bg-white p-4 rounded-lg border border-gray-200 space-y-3">
          <input
            type="text"
            placeholder="Task title..."
            value={formData.title}
            onChange={(e) => setFormData({ ...formData, title: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <textarea
            placeholder="Description..."
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 h-20"
          />
          <div className="grid grid-cols-3 gap-3">
            <select
              value={formData.priority}
              onChange={(e) => setFormData({ ...formData, priority: e.target.value })}
              className="px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
            <select
              value={formData.assigned_to_agent}
              onChange={(e) => setFormData({ ...formData, assigned_to_agent: e.target.value })}
              className="px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="rex">Rex</option>
              <option value="db_update_agent">DB Agent</option>
              <option value="pricing_agent">Pricing</option>
            </select>
            <select
              value={formData.category}
              onChange={(e) => setFormData({ ...formData, category: e.target.value })}
              className="px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="catalogue">Catalogue</option>
              <option value="pricing">Pricing</option>
              <option value="database">Database</option>
            </select>
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg">Create</button>
            <button onClick={() => setShowCreateForm(false)} className="bg-gray-300 hover:bg-gray-400 text-gray-800 px-4 py-2 rounded-lg">Cancel</button>
          </div>
        </div>
      )}

      <div className="flex gap-3 flex-wrap">
        <select
          value={filters.status || ''}
          onChange={(e) => setFilters({ ...filters, status: e.target.value || null })}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All Status</option>
          <option value="not_started">Not Started</option>
          <option value="in_progress">In Progress</option>
          <option value="completed">Completed</option>
          <option value="blocked">Blocked</option>
        </select>
        <select
          value={filters.agent || ''}
          onChange={(e) => setFilters({ ...filters, agent: e.target.value || null })}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All Agents</option>
          <option value="rex">Rex</option>
          <option value="db_update_agent">DB Agent</option>
          <option value="pricing_agent">Pricing</option>
        </select>
        <select
          value={filters.category || ''}
          onChange={(e) => setFilters({ ...filters, category: e.target.value || null })}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">All Categories</option>
          <option value="catalogue">Catalogue</option>
          <option value="pricing">Pricing</option>
          <option value="database">Database</option>
        </select>
      </div>

      {error && <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">Tasks Error: {error}</div>}

      <div className="space-y-2">
        {todos.length === 0 ? (
          <div className="bg-white p-8 rounded-lg text-center text-gray-500">No tasks found</div>
        ) : (
          todos.map((todo) => (
            <div key={todo.id} className="bg-white border border-gray-200 rounded-lg p-4 hover:shadow-md">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    {statusIcon(todo.status)}
                    <h3 className={`text-lg font-semibold ${priorityColor(todo.priority)}`}>{todo.title}</h3>
                    <span className={`text-xs px-2 py-1 rounded-full ${statusColor(todo.status)}`}>
                      {todo.status.replace(/_/g, ' ')}
                    </span>
                  </div>
                  {todo.description && <p className="text-sm text-gray-600 mb-2">{todo.description}</p>}
                  <div className="flex gap-3 text-xs text-gray-500">
                    <span>Category: {todo.category}</span>
                    {todo.assigned_to_agent && <span>Agent: {todo.assigned_to_agent}</span>}
                    {todo.target_date && <span>Due: {new Date(todo.target_date).toLocaleDateString()}</span>}
                  </div>
                  {todo.status !== 'completed' && (
                    <div className="mt-2">
                      <div className="w-full bg-gray-200 rounded-full h-2">
                        <div className="bg-blue-600 h-2 rounded-full" style={{ width: `${todo.progress_pct}%` }} />
                      </div>
                      <span className="text-xs text-gray-600">{todo.progress_pct}%</span>
                    </div>
                  )}
                </div>
                <div className="flex gap-2">
                  {todo.status !== 'completed' && (
                    <button
                      onClick={() => handleUpdate(todo.id, {
                        status: todo.status === 'in_progress' ? 'completed' : 'in_progress',
                        progress_pct: todo.status === 'in_progress' ? 100 : todo.progress_pct
                      })}
                      className="p-2 text-blue-600 hover:bg-blue-50 rounded"
                      title="Toggle progress"
                    >
                      <CheckCircle className="w-4 h-4" />
                    </button>
                  )}
                  <button onClick={() => handleDelete(todo.id)} className="p-2 text-red-600 hover:bg-red-50 rounded" title="Delete task">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-blue-50 p-4 rounded-lg border border-blue-200">
          <div className="text-2xl font-bold text-blue-600">{todos.filter((t) => t.status === 'in_progress').length}</div>
          <div className="text-sm text-gray-600">In Progress</div>
        </div>
        <div className="bg-green-50 p-4 rounded-lg border border-green-200">
          <div className="text-2xl font-bold text-green-600">{todos.filter((t) => t.status === 'completed').length}</div>
          <div className="text-sm text-gray-600">Completed</div>
        </div>
        <div className="bg-yellow-50 p-4 rounded-lg border border-yellow-200">
          <div className="text-2xl font-bold text-yellow-600">{todos.filter((t) => t.status === 'not_started').length}</div>
          <div className="text-sm text-gray-600">Not Started</div>
        </div>
        <div className="bg-red-50 p-4 rounded-lg border border-red-200">
          <div className="text-2xl font-bold text-red-600">{todos.filter((t) => t.status === 'blocked').length}</div>
          <div className="text-sm text-gray-600">Blocked</div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-xl font-semibold text-gray-900">Alias Review Queue</h3>
          <div className="flex items-center gap-2">
            <select
              value={reviewFilter}
              onChange={(e) => setReviewFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="">All</option>
            </select>
            <button
              onClick={fetchAliasReviews}
              className="px-3 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50"
            >
              Refresh
            </button>
          </div>
        </div>

        {aliasError && (
          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
            Alias Reviews Error: {aliasError}
          </div>
        )}

        {reviewLoading && aliasReviews.length === 0 ? (
          <div className="text-sm text-gray-500">Loading alias reviews...</div>
        ) : aliasReviews.length === 0 ? (
          <div className="text-sm text-gray-500">No alias review items found.</div>
        ) : (
          <div className="space-y-2">
            {aliasReviews.map((item) => (
              <div key={item.id} className="border border-gray-200 rounded-lg p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="text-sm text-gray-800">
                      <span className="font-semibold">{item.brand_name}</span>
                      <span className="mx-2 text-gray-400">-&gt;</span>
                      <span className="font-mono">{item.candidate_alias}</span>
                    </div>
                    <div className="text-xs text-gray-600 flex flex-wrap gap-3">
                      <span>Status: {item.status}</span>
                      {item.confidence !== null && <span>Confidence: {item.confidence.toFixed(4)}</span>}
                      {item.margin !== null && <span>Margin: {item.margin.toFixed(4)}</span>}
                      {item.reason && <span>Reason: {item.reason}</span>}
                    </div>
                  </div>

                  {item.status === 'pending' ? (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleAliasDecision(item.id, 'approve')}
                        disabled={reviewActionId === item.id}
                        className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-md bg-green-600 text-white hover:bg-green-700 disabled:opacity-60"
                      >
                        <CheckCircle className="w-4 h-4" /> Approve
                      </button>
                      <button
                        onClick={() => handleAliasDecision(item.id, 'reject')}
                        disabled={reviewActionId === item.id}
                        className="inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-60"
                      >
                        <XCircle className="w-4 h-4" /> Reject
                      </button>
                    </div>
                  ) : (
                    <span className={`text-xs px-2 py-1 rounded-full ${statusColor(item.status)}`}>
                      {item.status}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default AgentTodoList;

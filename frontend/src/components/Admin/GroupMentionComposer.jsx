import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { Loader2, Send, RefreshCw } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '../../api/client'
import { activeMention, filterMembers, groupErrorText, insertMention, toWireText } from './mentionLogic'

// Composer for the WhatsApp updates group with real @mention autocomplete. Members come from the live group metadata
// (GET /admin/whatsapp/group-members); the server maps the opaque tokens to real WhatsApp mention JIDs.
export default function GroupMentionComposer() {
  const [members, setMembers] = useState([])
  const [subject, setSubject] = useState('')
  const [state, setState] = useState('loading') // loading | ready | disabled | error
  const [errorText, setErrorText] = useState('')
  const [text, setText] = useState('')
  const [chosen, setChosen] = useState([])
  const [mention, setMention] = useState(null)
  const [active, setActive] = useState(0)
  const [sending, setSending] = useState(false)
  const ref = useRef(null)
  const listId = useId()

  const load = useCallback(async (refresh = false) => {
    setState('loading')
    try {
      const { data } = await api.get('/admin/whatsapp/group-members', { params: { refresh } })
      setMembers(data.members || []); setSubject(data.subject || ''); setState('ready')
    } catch (e) {
      const st = e?.response?.status
      setErrorText(groupErrorText(st, e?.response?.data?.detail))
      setState(st === 403 ? 'disabled' : 'error')
    }
  }, [])
  useEffect(() => { load() }, [load])

  const options = useMemo(() => (mention ? filterMembers(members, mention.query) : []), [members, mention])
  useEffect(() => { setActive(0) }, [mention?.query])

  const refreshMention = (value, caret) => setMention(activeMention(value, caret))
  const choose = (member) => {
    if (!mention) return
    const r = insertMention(text, mention, member)
    setText(r.text); setMention(null)
    setChosen((c) => (c.some((x) => x.token === member.token) ? c : [...c, member]))
    requestAnimationFrame(() => { ref.current?.focus(); ref.current?.setSelectionRange(r.caret, r.caret) })
  }
  const onKeyDown = (e) => {
    if (!mention || (!options.length && e.key !== 'Escape')) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => (a + 1) % options.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => (a - 1 + options.length) % options.length) }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); choose(options[active]) }
    else if (e.key === 'Escape') { e.preventDefault(); setMention(null) }
  }
  const send = async () => {
    const wire = toWireText(text.trim(), chosen)
    if (!wire.text) return
    setSending(true)
    try {
      await api.post('/admin/whatsapp/group-message', { text: wire.text })
      toast.success(wire.mentions.length ? `נשלח (${wire.mentions.length} אזכורים)` : 'נשלח')
      setText(''); setChosen([]); setMention(null)
    } catch (e) {
      if (e?.response?.data?.detail === 'unknown or removed participant') { toast.error('משתתף שהוזכר כבר לא בקבוצה — הרשימה רועננה'); load(true) }
      else toast.error(groupErrorText(e?.response?.status, e?.response?.data?.detail) || 'השליחה נכשלה')
    } finally { setSending(false) }
  }

  if (state === 'disabled') return <p className="text-sm text-slate-400">הודעה לקבוצה כבויה (WA_GROUP_COMPOSER_ENABLED).</p>
  return (
    <div className="space-y-2" dir="rtl">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={`${listId}-ta`} className="text-sm font-medium text-slate-200">הודעה לקבוצה{subject ? ` · ${subject}` : ''}</label>
        <button type="button" onClick={() => load(true)} aria-label="רענן רשימת חברי קבוצה" className="btn-ghost text-xs inline-flex items-center gap-1 min-h-[44px] px-3">
          {state === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} רענן
        </button>
      </div>
      {state === 'error' && <p role="alert" className="text-sm text-red-400">{errorText || 'טעינת החברים נכשלה — לחץ רענן'}</p>}
      <div className="relative">
        <textarea
          id={`${listId}-ta`} ref={ref} rows={3} value={text} maxLength={2000} dir="auto"
          className="input-field w-full resize-y"
          placeholder="כתוב הודעה… הקלד @ כדי לאזכר חבר קבוצה"
          role="combobox" aria-expanded={!!mention} aria-controls={listId} aria-autocomplete="list"
          aria-activedescendant={mention && options[active] ? `${listId}-o${active}` : undefined}
          onChange={(e) => { setText(e.target.value); refreshMention(e.target.value, e.target.selectionStart) }}
          onClick={(e) => refreshMention(text, e.target.selectionStart)}
          onKeyUp={(e) => { if (!['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(e.key)) refreshMention(e.target.value, e.target.selectionStart) }}
          onKeyDown={onKeyDown}
          onBlur={() => setTimeout(() => setMention(null), 150)}
        />
        {mention && (
          <ul id={listId} role="listbox" aria-label="חברי הקבוצה"
              className="absolute z-20 mt-1 w-full max-h-56 overflow-y-auto rounded-lg border border-brand-border bg-brand-200 shadow-lg">
            {state === 'loading' && <li className="px-3 py-3 text-sm text-slate-400">טוען…</li>}
            {state === 'error' && <li className="px-3 py-3 text-sm text-red-400">{errorText || 'טעינת החברים נכשלה — לחץ רענן'}</li>}
            {state === 'ready' && !options.length && <li className="px-3 py-3 text-sm text-slate-400">אין חבר קבוצה תואם</li>}
            {options.map((m, i) => (
              <li key={m.token} id={`${listId}-o${i}`} role="option" aria-selected={i === active}
                  onMouseDown={(e) => { e.preventDefault(); choose(m) }} onMouseEnter={() => setActive(i)}
                  className={`flex items-center gap-3 px-3 min-h-[44px] cursor-pointer text-sm text-slate-100 ${i === active ? 'bg-brand-100 outline outline-2 outline-sky-500' : ''}`}>
                <span aria-hidden="true" className="w-7 h-7 shrink-0 rounded-full bg-sky-500/20 text-sky-300 flex items-center justify-center text-xs">
                  {m.name.trim().slice(0, 1)}
                </span>
                <span className="truncate" dir="auto">{m.name}</span>
                {m.admin && <span className="text-xs text-slate-400 ms-auto">מנהל</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" onClick={send} disabled={sending || !text.trim()} className="btn-primary text-sm inline-flex items-center gap-2 min-h-[44px] disabled:opacity-60">
        {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} שלח לקבוצה
      </button>
    </div>
  )
}

SELECT 'auth', count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) FROM agent_authorizations t
UNION ALL SELECT 'outreach', count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) FROM supplier_outreach t
UNION ALL SELECT 'proc', count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) FROM procurement_orders t
UNION ALL SELECT 'events', count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) FROM procurement_events t
UNION ALL SELECT 'email_cols', count(*), md5(coalesce(string_agg(id::text||'/'||source_folder||'/'||coalesce(supplier_routed_at::text,'')||'/'||coalesce(supplier_route::text,''), '|' ORDER BY id), '')) FROM email_agent_messages WHERE gmail_message_id NOT LIKE 'rehearsal-%'
UNION ALL SELECT 'events_seq', last_value, is_called::text FROM procurement_events_id_seq;

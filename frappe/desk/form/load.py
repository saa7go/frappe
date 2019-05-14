# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import frappe, json
import frappe.utils
import frappe.share
import frappe.defaults
import frappe.desk.form.meta
from frappe.model.utils.user_settings import get_user_settings
from frappe.permissions import get_doc_permissions
from frappe.desk.form.document_follow import is_document_followed
from frappe import _

@frappe.whitelist()
def getdoc(doctype, name, user=None):
	"""
	Loads a doclist for a given document. This method is called directly from the client.
	Requries "doctype", "name" as form variables.
	Will also call the "onload" method on the document.
	"""

	if not (doctype and name):
		raise Exception('doctype and name required!')

	if not name:
		name = doctype

	if not frappe.db.exists(doctype, name):
		return []

	try:
		doc = frappe.get_doc(doctype, name)
		run_onload(doc)

		if not doc.has_permission("read"):
			frappe.flags.error_message = _('Insufficient Permission for {0}').format(frappe.bold(doctype + ' ' + name))
			raise frappe.PermissionError(("read", doctype, name))

		doc.apply_fieldlevel_read_permissions()

		# add file list
		doc.add_viewed()
		get_docinfo(doc)

	except Exception:
		frappe.errprint(frappe.utils.get_traceback())
		raise

	doc.add_seen()

	frappe.response.docs.append(doc)

@frappe.whitelist()
def getdoctype(doctype, with_parent=False, cached_timestamp=None):
	"""load doctype"""

	docs = []
	parent_dt = None

	# with parent (called from report builder)
	if with_parent:
		parent_dt = frappe.model.meta.get_parent_dt(doctype)
		if parent_dt:
			docs = get_meta_bundle(parent_dt)
			frappe.response['parent_dt'] = parent_dt

	if not docs:
		docs = get_meta_bundle(doctype)

	frappe.response['user_settings'] = get_user_settings(parent_dt or doctype)

	if cached_timestamp and docs[0].modified==cached_timestamp:
		return "use_cache"

	frappe.response.docs.extend(docs)

def get_meta_bundle(doctype):
	bundle = [frappe.desk.form.meta.get_meta(doctype)]
	for df in bundle[0].fields:
		if df.fieldtype in frappe.model.table_fields:
			bundle.append(frappe.desk.form.meta.get_meta(df.options, not frappe.conf.developer_mode))
	return bundle

@frappe.whitelist()
def get_docinfo(doc=None, doctype=None, name=None):
	if not doc:
		doc = frappe.get_doc(doctype, name)
		if not doc.has_permission("read"):
			raise frappe.PermissionError
	frappe.response["docinfo"] = {
		"attachments": get_attachments(doc.doctype, doc.name),
		"communications": _get_communications(doc.doctype, doc.name),
		'comments': get_comments(doc.doctype, doc.name),
		'total_comments': len(json.loads(doc.get('_comments') or '[]')),
		'versions': get_versions(doc),
		"assignments": get_assignments(doc.doctype, doc.name),
		"permissions": get_doc_permissions(doc),
		"shared": frappe.share.get_users(doc.doctype, doc.name),
		"views": get_view_logs(doc.doctype, doc.name),
		"energy_point_logs": get_point_logs(doc.doctype, doc.name),
		"milestones": get_milestones(doc.doctype, doc.name),
		"is_document_followed": is_document_followed(doc.doctype, doc.name, frappe.session.user)
	}

def get_milestones(doctype, name):
	return frappe.db.get_all('Milestone', fields = ['creation', 'owner', 'track_field', 'value'],
		filters=dict(reference_type=doctype, reference_name=name))

def get_attachments(dt, dn):
	return frappe.get_all("File", fields=["name", "file_name", "file_url", "is_private"],
		filters = {"attached_to_name": dn, "attached_to_doctype": dt})

def get_versions(doc):
	return frappe.get_all('Version', filters=dict(ref_doctype=doc.doctype, docname=doc.name),
		fields=['name', 'owner', 'creation', 'data'], limit=10, order_by='creation desc')

@frappe.whitelist()
def get_communications(doctype, name, start=0, limit=20):
	doc = frappe.get_doc(doctype, name)
	if not doc.has_permission("read"):
		raise frappe.PermissionError

	return _get_communications(doctype, name, start, limit)


def get_comments(doctype, name):
	comments = frappe.get_all('Comment', fields = ['*'], filters = dict(
		reference_doctype = doctype,
		reference_name = name
	))

	# convert to markdown (legacy ?)
	for c in comments:
		if c.comment_type == 'Comment':
			c.content = frappe.utils.markdown(c.content)

	return comments

def get_point_logs(doctype, docname):
	return frappe.db.get_all('Energy Point Log', filters={
		'reference_doctype': doctype,
		'reference_name': docname,
		'type': ['!=', 'Review']
	}, fields=['*'])

def _get_communications(doctype, name, start=0, limit=20):
	communications = get_communication_data(doctype, name, start, limit)
	for c in communications:
		if c.communication_type=="Communication":
			c.attachments = json.dumps(frappe.get_all("File",
				fields=["file_url", "is_private"],
				filters={"attached_to_doctype": "Communication",
					"attached_to_name": c.name}
				))

	return communications

def get_communication_data(doctype, name, start=0, limit=20, after=None, fields=None,
	group_by=None, as_dict=True):
	'''Returns list of communications for a given document'''
	if not fields:
		fields = '''
			`tabCommunication`.name, `tabCommunication`.communication_type, `tabCommunication`.communication_medium,
			`tabCommunication`.comment_type, `tabCommunication`.communication_date, `tabCommunication`.content,
			`tabCommunication`.sender, `tabCommunication`.sender_full_name, `tabCommunication`.cc, `tabCommunication`.bcc,
			`tabCommunication`.creation, `tabCommunication`.subject, `tabCommunication`.delivery_status,
			`tabCommunication`._liked_by, `tabCommunication`.reference_doctype, `tabCommunication`.reference_name,
			`tabCommunication`.link_doctype, `tabCommunication`.link_name, `tabCommunication`.read_by_recipient,
			`tabCommunication`.rating, `tabDynamic Link`.link_doctype, `tabDynamic Link`.link_name
		'''

	conditions = '''
		`tabCommunication`.communication_type in ('Communication', 'Feedback')
		and	(
				(`tabCommunication`.reference_doctype=%(doctype)s and `tabCommunication`.reference_name=%(name)s)
				or (
					(`tabDynamic Link`.link_doctype=%(doctype)s and `tabDynamic Link`.link_name=%(name)s)
					and (`tabCommunication`.communication_type='Communication')
				)
			)
	'''

	if not group_by:
		group_by = '''
			group by `tabCommunication`.name
		'''

	if after:
		# find after a particular date
		conditions += '''
			and `tabCommunication`.creation > {0}
		'''.format(after)

	if doctype=='User':
		conditions += '''
			and not (`tabCommunication`.reference_doctype='User' and `tabCommunication`.communication_type='Communication')
		'''

	communications = frappe.db.sql('''
		select {fields}
		from `tabCommunication`
			left join `tabDynamic Link`
				on `tabCommunication`.name=`tabDynamic Link`.parent
		where {conditions} {group_by}
		order by `tabCommunication`.creation desc
		limit %(limit)s offset %(start)s'''.format(
			fields = fields,
			conditions=conditions,
			group_by=group_by or ""
		),{
			"doctype": doctype,
			"name": name,
			"start": frappe.utils.cint(start),
			"limit": limit
		}, as_dict=as_dict, debug=True)
	print(communications)
	return communications

def get_assignments(dt, dn):
	cl = frappe.db.sql("""select `name`, owner, description from `tabToDo`
		where reference_type=%(doctype)s and reference_name=%(name)s and status='Open'
		order by modified desc limit 5""", {
			"doctype": dt,
			"name": dn
		}, as_dict=True)

	return cl

@frappe.whitelist()
def get_badge_info(doctypes, filters):
	filters = json.loads(filters)
	doctypes = json.loads(doctypes)
	filters["docstatus"] = ["!=", 2]
	out = {}
	for doctype in doctypes:
		out[doctype] = frappe.db.get_value(doctype, filters, "count(*)")

	return out

def run_onload(doc):
	doc.set("__onload", frappe._dict())
	doc.run_method("onload")

def get_view_logs(doctype, docname):
	""" get and return the latest view logs if available """
	logs = []
	if hasattr(frappe.get_meta(doctype), 'track_views') and frappe.get_meta(doctype).track_views:
		view_logs = frappe.get_all("View Log", filters={
			"reference_doctype": doctype,
			"reference_name": docname,
		}, fields=["name", "creation", "owner"], order_by="creation desc")

		if view_logs:
			logs = view_logs
	return logs
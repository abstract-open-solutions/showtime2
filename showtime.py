#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from flask import Flask, render_template, request, jsonify, url_for, \
    redirect, send_file, abort, session
from remoteTimereg import RemoteTimereg
from odooTimereg import OdooTimereg
from datetime import datetime, timedelta, date
from cStringIO import StringIO
import os
import csv
import itsdangerous
import requests

app = Flask(__name__)
app.config.from_envvar("SHOWTIME_SETTINGS")
app.jinja_env.add_extension('pyjade.ext.jinja.PyJadeExtension')

##################################
## Achievo response XML parsers ##
##################################

## Needed only if you extract the parson module
#from xml.etree import ElementTree as ET
#import datetime


def parseProjects(pdict):
    projects = []
    for element in pdict:
        projects.append(element.get("name"))
    return projects

def parseHours(etree, encoding):
    hours = []
    for element in etree:
        hours.append(
            {
                "project": element.get("project"),
                "date": datetime.strptime(element.get("date"), "%Y-%m-%d").date(),
                "time": timedelta(minutes = int(element.get("time"))),
                "remark": element.get("remark").decode(encoding),
                "activity": element.get("activity"),
                "phase": element.get("phase"),
                "user": element.get("user"),
            }
        )
    return hours

def dvlrit(url):
    r = requests.post(app.config["DVLRIT_URL"] + "/short_url", timeout=3.0, params={
        "q": url
    })
    return r.content[14:-3]

##################################
# CSRF protection
# http://flask.pocoo.org/snippets/3/

@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.pop('_csrf_token', None)
        if not token or token != request.json.get('_csrf_token'):
            abort(403)

def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = os.urandom(16).encode("hex")
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

##################################
# Views
#

@app.route('/createlink', methods=["POST"])
def createlink():
    import ipdb;ipdb.set_trace()
    projects = request.json['projects']
    expire = request.json['expire']

    if not expire:
        expire = (date.today() + timedelta(365)).strftime("%Y-%m-%d")

    s = itsdangerous.URLSafeSerializer(app.config["SECRET_KEY"])
    token = s.dumps({
        'projects': projects,
        'expire': expire,
    })

    url = url_for('view', token=token, _external=True)
    try:
        url = dvlrit(url)
    except:
        pass

    return jsonify({
        'link': url
    })


class MonthDate(object):
    """
    Store and represent the couple "year+month" (without day).
    """
    def __init__(self, year, month):
        self.year = year
        self.month = month

    def prev(self):
        y = self.year
        m = self.month - 1
        if m == 0:
            y -= 1
            m = 12
        return MonthDate(y, m)

    def next(self):
        y = self.year
        m = self.month + 1
        if m == 13:
            y += 1
            m = 1
        return MonthDate(y, m)

    @staticmethod
    def fromdate(date):
        return MonthDate(date.year, date.month)

    @staticmethod
    def fromstring(s):
        parts = s.split("-")
        return MonthDate(int(parts[0]), int(parts[1]))

    @staticmethod
    def today():
        today = date.today()
        return MonthDate(today.year, today.month)

    def englishformat(self):
        return self.topython().strftime("%B %Y")

    def topython(self):
        return date(self.year, self.month, 1)

    def __str__(self):
        return "%04d-%02d" % (self.year, self.month)


@app.route('/view/<token>')
def view(token):
    import ipdb;ipdb.set_trace()
    s = itsdangerous.URLSafeSerializer(app.config["SECRET_KEY"])
    try:
        data = s.loads(token)
    except:
        abort(404)

    expire = datetime.strptime(data["expire"], "%Y-%m-%d")
    if expire <= datetime.today():
        return render_template("view_error.jade", error="EXPIRED"), 403

    o = OdooTimereg()
    client = o.login(
        app.config["ODOO_URI"],
        app.config["ODOO_USER"],
        app.config["ODOO_PASSWORD"],
        app.config["ODOO_DB"]
    )

    if "date" in request.args:
        from_date = MonthDate.fromstring(request.args["date"])
    else:
        from_date = MonthDate.today().prev()

    to_date = from_date.next()
    hours = parseHours(
        client.hours(
            data["projects"], from_date.topython(), to_date.topython()),
        app.config["ACHIEVO_ENCODING"])

    # Filter non billable hours
    hours = [h for h in hours if h["phase"] != "non billable"]

    total = 0
    for h in hours:
        s = h["time"].total_seconds()
        h["time"] = "%dh %dm" % (s // 3600, (s % 3600) // 60)
        total += s
    total = "%dh %dm" % (total // 3600, (total % 3600) // 60)

    if "csv" in request.args:
        string = StringIO()
        writer = csv.writer(string)
        writer.writerow(["Project", "Phase", "Date", "User", "Remark", "Time"])
        for hour in hours:
            writer.writerow([
                hour["project"],
                hour["phase"],
                hour["date"].strftime("%d %b %Y"),
                hour["user"],
                hour["remark"].encode("utf-8"),
                hour["time"]
            ])
        string.seek(0)
        fn = "develer-%s-%s.csv" % ("-".join(data["projects"]), from_date.englishformat())
        return send_file(
            string,
            attachment_filename=fn,
            as_attachment=True)

    prev_url = url_for("view", token=token, date=str(from_date.prev()))
    next_url = url_for("view", token=token, date=str(from_date.next()))
    cur_month = from_date.englishformat()

    num_projects = len(data["projects"])
    project_name = ", ".join(data["projects"])
    csv_url = url_for("view", token=token, date=from_date, csv=True)

    return render_template("view.jade",
        project_name=project_name, num_projects=num_projects,
        hours=hours, total=total, cur_month=cur_month,
        prev_url=prev_url, next_url=next_url, csv_url=csv_url)

@app.route('/create')
def create():
    o = OdooTimereg()
    client = o.login(
        app.config["ODOO_URI"],
        app.config["ODOO_USER"],
        app.config["ODOO_PASSWORD"],
        app.config["ODOO_DB"]
    )
    projects = parseProjects(o.projects(client))
    return render_template('create.jade', projects=projects)

@app.route('/')
def index():
    return redirect(url_for("create"))

if __name__ == '__main__':
    app.run()

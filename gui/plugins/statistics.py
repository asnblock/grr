#!/usr/bin/env python
# Copyright 2011 Google Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""GUI elements to display general statistics."""


import time

from grr.gui import renderers
from grr.lib import aff4
from grr.lib import rdfvalue


class ShowStatistics(renderers.Splitter2WayVertical):
  """View various statistics."""

  description = "Show Statistics"
  behaviours = frozenset(["General"])

  left_renderer = "StatsTree"
  right_renderer = "ReportRenderer"


class ReportRenderer(renderers.TemplateRenderer):
  """A renderer for Statistic Reports."""

  layout_template = renderers.Template("""
<div class="padded">
  {% if not this.delegated_renderer %}
    <h3>Select a statistic to view.</h3>
  {% endif %}
  <div id="{{unique|escape}}"></div>
</div>
<script>
  grr.subscribe("tree_select", function(path) {
    grr.state.path = path
    $("#{{id|escapejs}}").html("<em>Loading&#8230;</em>");
    grr.layout("{{renderer|escapejs}}", "{{id|escapejs}}");
  }, "{{unique|escapejs}}");
</script>
""")

  def Layout(self, request, response):
    """Delegate to a stats renderer if needed."""
    path = request.REQ.get("path", "")

    # Try and find the correct renderer to use.
    for cls in self.classes.values():
      if getattr(cls, "category", None) == path:
        self.delegated_renderer = cls()

        # Render the renderer directly here
        self.delegated_renderer.Layout(request, response)
        break

    return super(ReportRenderer, self).Layout(request, response)


class StatsTree(renderers.TreeRenderer):
  """Show all the available reports."""

  def GetStatsClasses(self):
    classes = []

    for cls in self.classes.values():
      if aff4.issubclass(cls, Report) and cls.category:
        classes.append(cls.category)

    classes.sort()
    return classes

  def RenderBranch(self, path, _):
    """Show all the stats available."""
    for category_name in self.GetStatsClasses():
      if category_name.startswith(path):
        elements = filter(None, category_name[len(path):].split("/"))

        # Do not allow duplicates
        if elements[0] in self: continue

        if len(elements) > 1:
          self.AddElement(elements[0], "branch")
        elif elements:
          self.AddElement(elements[0], "leaf")


class Report(renderers.TemplateRenderer):
  """This is the base of all Statistic Reports."""
  category = None


class PieChart(Report):
  """Display a pie chart."""

  layout_template = renderers.Template("""
<div class="padded">
{% if this.graph %}
  <h3>{{this.title|escape}}</h3>
  <div>
  {{this.description|escape}}
  </div>
  <div id="hover">Hover to show exact numbers.</div>
  <div id="{{unique|escape}}" class="grr_graph"></div>
  <script>

  var specs = [
    {% for data in this.graph %}
    {label: "{{data.label|escapejs}}", data: {{data.y_value|escapejs}} },
    {% endfor %}
  ];

  $("#{{unique|escapejs}}").resize(function () {
    $("#{{unique|escapejs}}").html("");
    $.plot($("#{{unique|escapejs}}"), specs, {
      series: {
        pie: {
          show: true,
          label: {
            show: true,
            radius: 0.5,
            formatter: function(label, series){
              return ('<div style="font-size:8pt;' +
                      'text-align:center;padding:2px;color:white;">' +
                      label+'<br/>'+Math.round(series.percent)+'%</div>');
            },
            background: { opacity: 0.8 }
          }
        }
      },
      grid: {
        hoverable: true,
        clickable: true
      }
    });
  });

  $("#{{unique|escapejs}}").bind("plothover", function(event, pos, obj) {
    if (obj) {
      percent = parseFloat(obj.series.percent).toFixed(2);
      $("#hover").html('<span style="font-weight: bold; color: ' +
                       obj.series.color + '">' + obj.series.label + " " +
                       obj.series[0][1] + ' (' + percent + '%)</span>');
    }
  });

  $("#{{unique|escapejs}}").resize();
  </script>
{% else %}
  <h3>No data Available</h3>
{% endif %}
</div>
""")


class OSBreakdown(PieChart):
  category = "/Clients/OS Breakdown/ 1 Day Active"
  title = "Operating system break down."
  description = "OS breakdown for clients that were active in the last day."
  active_day = 1
  attribute = aff4.OSBreakDown.SchemaCls.OS_HISTOGRAM

  def Layout(self, request, response):
    """Extract only the operating system type from the active histogram."""
    try:
      fd = aff4.FACTORY.Open("cron:/OSBreakDown", token=request.token)
      self.graph = rdfvalue.Graph(title="Operating system break down.")
      for graph in fd.Get(self.attribute):
        # Find the correct graph and merge the OS categories together
        if "%s day" % self.active_day in graph.title:
          for sample in graph:
            self.graph.Append(label=sample.label, y_value=sample.y_value)
          break
    except (IOError, TypeError):
      pass

    return super(OSBreakdown, self).Layout(request, response)


class OSBreakdown7(OSBreakdown):
  category = "/Clients/OS Breakdown/ 7 Day Active"
  description = "OS breakdown for clients that were active in the last week."
  active_day = 7


class OSBreakdown30(OSBreakdown):
  category = "/Clients/OS Breakdown/30 Day Active"
  description = "OS breakdown for clients that were active in the last month."
  active_day = 30


class ReleaseBreakdown(OSBreakdown):
  category = "/Clients/OS Release Breakdown/ 1 Day Active"
  title = "Operating system version break down."
  description = "This plot shows what OS clients active within the last day."
  active_day = 1
  attribute = aff4.OSBreakDown.SchemaCls.VERSION_HISTOGRAM


class ReleaseBreakdown7(ReleaseBreakdown):
  category = "/Clients/OS Release Breakdown/ 7 Day Active"
  description = "What OS Version clients were active within the last week."
  active_day = 7


class ReleaseBreakdown30(ReleaseBreakdown):
  category = "/Clients/OS Release Breakdown/30 Day Active"
  description = "What OS Version clients were active within the last month."
  active_day = 30


class LastActiveReport(OSBreakdown):
  """Display a histogram of last actives."""
  category = "/Clients/Last Active/Count of last activity time"
  title = "Breakdown of Client Count Based on Last Activity of the Client."
  description = """
This plot shows the number of clients active in the last day and how that number
evolves over time.
"""
  active_days_display = [1, 3, 7, 30, 60]
  attribute = aff4.LastAccessStats.SchemaCls.HISTOGRAM
  DATA_URN = "cron:/LastAccessStats"

  layout_template = renderers.Template("""
<div class="padded">
{% if this.graphs %}
  <h3>{{this.title|escape}}</h3>
  <div id="{{unique|escape}}_click">
    {{this.description|escape}}
  </div>
  <div id="{{unique|escape}}" class="grr_graph"></div>
  <script>
    var specs = [];

  {% for graph in this.graphs %}
    specs.push({
      label: "{{graph.title|escapejs}}",
      data: [
  {% for series in graph %}
        [ {{series.x_value|escapejs}}, {{series.y_value|escapejs}}],
  {% endfor %}
      ],
    });
  {% endfor %}

    var options = {
      xaxis: {mode: "time",
              timeformat: "%y/%m/%d"},
      lines: {show: true},
      points: {show: true},
      zoom: {interactive: true},
      pan: {interactive: true},
      grid: {clickable: true, autohighlight: true},
    };

    var placeholder = $("#{{unique|escapejs}}");
    var plot = $.plot(placeholder, specs, options);

    placeholder.bind("plotclick", function(event, pos, item) {
      if (item) {
        var date = new Date(item.datapoint[0]);
        $("#{{unique|escapejs}}_click").text("On " + date.toDateString() +
          ", there were " + item.datapoint[1] + " " + item.series.label +
          " systems.");
      };
    });
  </script>
{% else %}
  <h3>No data Available</h3>
{% endif %}
</div>
""")

  def Layout(self, request, response):
    """Show how the last active breakdown evolves over time."""
    try:
      fd = aff4.FACTORY.Open(self.DATA_URN, token=request.token,
                             age=self._GetAgeTupleFromRequest(request, 180))
      categories = {}
      for graph_series in fd.GetValuesForAttribute(self.attribute):
        for graph in graph_series:
          # Find the correct graph and merge the OS categories together
          for sample in graph:
            # Provide the time in js timestamps (millisecond since the epoch)
            days = sample.x_value/1000000/24/60/60
            if days in self.active_days_display:
              label = "%s day active" % days
              categories.setdefault(label, []).append(
                  (graph_series.age/1000, sample.y_value))

      self.graphs = []
      for k, v in categories.items():
        graph = rdfvalue.Graph(title=k)
        for x, y in v:
          graph.Append(x_value=x, y_value=y)
        self.graphs.append(graph)
    except IOError:
      pass

    return Report.Layout(self, request, response)

  def _GetAgeTupleFromRequest(self, request, default_days=90):
    """Check the request for start/end times and return aff4 age tuple."""
    now = int(time.time() * 1e6)
    default_start = now - (60*60*24*1e6*default_days)
    self.start_time = int(request.REQ.get("start_time", default_start))
    self.end_time = int(request.REQ.get("end_time", now))
    return (self.start_time, self.end_time)


class LastDayGRRVersionReport(LastActiveReport):
  """Display a histogram of last actives based on GRR Version."""
  category = "/Clients/GRR Version/ 1 Day"
  title = "One day Active Clients."
  description = """This shows the number of clients active in the last day based
on the GRR version.
"""
  DATA_URN = "cron:/GRRVersionBreakDown"
  attribute = aff4.GRRVersionBreakDown.SchemaCls.GRRVERSION_HISTOGRAM

  def Layout(self, request, response):
    """Show how the last active breakdown evolves over time."""
    try:
      fd = aff4.FACTORY.Open(self.DATA_URN, token=request.token,
                             age=self._GetAgeTupleFromRequest(request, 90))
      categories = {}
      for graph_series in fd.GetValuesForAttribute(self.attribute):
        for graph in graph_series:
          # Find the correct graph and merge the OS categories together
          if "%s day" % self.active_day in graph.title:
            for sample in graph:
              # Provide the time in js timestamps (millisecond since the epoch)
              categories.setdefault(sample.label, []).append(
                  (graph_series.age/1000, sample.y_value))
            break

      self.graphs = []
      for k, v in categories.items():
        graph = rdfvalue.Graph(title=k)
        for x, y in v:
          graph.Append(x_value=x, y_value=y)

        self.graphs.append(graph)
    except IOError:
      pass

    return Report.Layout(self, request, response)


class Last7DaysGRRVersionReport(LastDayGRRVersionReport):
  """Display a histogram of last actives based on GRR Version."""
  category = "/Clients/GRR Version/ 7 Day"
  title = "7 day Active Clients."
  description = """This shows the number of clients active in the last 7 days
based on the GRR version.
"""
  active_day = 7


class Last30DaysGRRVersionReport(LastDayGRRVersionReport):
  """Display a histogram of last actives based on GRR Version."""
  category = "/Clients/GRR Version/ 30 Day"
  title = "30 day Active Clients."
  description = """This shows the number of clients active in the last 30 days
based on the GRR version.
"""
  active_day = 30


class StatGraph(object):
  def __init__(self):
    self.series = []


class StatData(object):
  def __init__(self, label, data):
    self.data = data
    self.label = label


class AFF4ClientStats(Report):
  """A renderer for client stats graphs."""

  # This renderer will render ClientStats AFF4 objects.
  aff4_type = "ClientStats"

  layout_template = renderers.Template("""
<div class="padded">
{% if this.graphs %}
<script>
selectTab = function (tabid) {
  {% for graph in this.graphs %}
      $("#{{unique|escapejs}}_{{graph.id|escapejs}}")[0]
          .style.display = "none";
      $("#{{unique|escapejs}}_{{graph.id|escapejs}}_a")
          .removeClass("selected");
  {% endfor %}

  $("#{{unique|escapejs}}_" + tabid)[0].style.display = "block";
  $("#{{unique|escapejs}}_click").text("");
  $("#{{unique|escapejs}}_" + tabid + "_a").addClass("selected");

  $("#{{unique|escapejs}}_" + tabid)[0].style.visibility = "hidden";
  $("#{{id|escapejs}}").resize();
  p = eval("plot_" + tabid);
  p.resize();
  p.setupGrid();
  p.draw();
  $("#{{unique|escapejs}}_" + tabid)[0].style.visibility = "visible";
};
</script>

{% for graph in this.graphs %}
  <a id="{{unique|escape}}_{{graph.id|escape}}_a"
     onClick='selectTab("{{graph.id|escape}}");'>{{graph.name|escape}}</a> |
{% endfor %}
<br><br>
<div id="{{unique|escape}}_click"><br></div><br>
<div id="{{unique|escape}}_graphs" style="height:100%;">
{% for graph in this.graphs %}
  <div id="{{unique|escape}}_{{graph.id|escape}}" class="grr_graph"></div>
  <script>
      var specs_{{graph.id|escapejs}} = [];

  {% for stats in graph.series %}
    specs_{{graph.id|escapejs}}.push({
      label: "{{stats.label|escapejs}}",
      data: [
        {{stats|escapejs}}
      ],
    });
  {% endfor %}
    var options_{{graph.id|escapejs}} = {
      xaxis: {mode: "time",
              timeformat: "%y/%m/%d - %H:%M:%S"},
      lines: {show: true},
      points: {show: true},
      zoom: {interactive: true},
      pan: {interactive: true},
      grid: {clickable: true, autohighlight: true},
    };

    var placeholder = $("#{{unique|escapejs}}_{{graph.id|escapejs}}");
    var plot_{{graph.id|escapejs}} = $.plot(
            placeholder, specs_{{graph.id|escapejs}},
            options_{{graph.id|escapejs}});

    placeholder.bind("plotclick", function(event, pos, item) {
      if (item) {
        var date = new Date(item.datapoint[0]);
        var msg = "{{graph.click_text|escapejs}}";
        msg = msg.replace("%date", date.toString())
        msg = msg.replace("%value", item.datapoint[1])
        $("#{{unique|escapejs}}_click").text(msg);
      };
    });
  </script>
  {% endfor %}
</div>
<script>
  selectTab("cpu");
</script>
{% else %}
  <h3>No data Available</h3>
{% endif %}
</div>
""")

  def __init__(self, fd=None, **kwargs):
    if fd:
      self.fd = fd
    super(AFF4ClientStats, self).__init__(**kwargs)

  def Layout(self, request, response):
    """This renders graphs for the various client statistics."""

    self.client_id = request.REQ.get("client_id")

    fd = aff4.FACTORY.Open("aff4:/%s/stats" % self.client_id,
                           token=request.token, age=aff4.ALL_TIMES)

    self.graphs = []

    stats = fd.GetValuesForAttribute(fd.Schema.STATS)
    if not stats:
      return super(AFF4ClientStats, self).Layout(request, response)

    # CPU usage graph.
    graph = StatGraph()
    series = dict()
    for stat_entry in stats:
      for s in stat_entry.cpu_samples:
        series[int(s.timestamp/1e3)] = s.cpu_percent
    data = [[k, series[k]] for k in sorted(series)]
    graph.series = [StatData("CPU Usage in %", ",".join(map(str, data)))]
    graph.name = "CPU Usage"
    graph.id = "cpu"
    graph.click_text = "CPU usage on %date: %value"
    self.graphs.append(graph)

    # IO graphs.
    graph = StatGraph()
    series = dict()
    for stat_entry in stats:
      for s in stat_entry.io_samples:
        series[int(s.timestamp/1e3)] = int(s.read_bytes)
    data = [[k, series[k]] for k in sorted(series)]
    graph.series = [StatData("IO Bytes Read", ",".join(map(str, data)))]
    graph.name = "IO Bytes Read"
    graph.id = "io_read"
    graph.click_text = "Number of bytes received (IO) until %date: %value"
    self.graphs.append(graph)

    graph = StatGraph()
    series = dict()
    for stat_entry in stats:
      for s in stat_entry.io_samples:
        series[int(s.timestamp/1e3)] = int(s.write_bytes)
    data = [[k, series[k]] for k in sorted(series)]
    graph.series = [StatData("IO Bytes Written", ",".join(map(str, data)))]
    graph.name = "IO Bytes Written"
    graph.id = "io_write"
    graph.click_text = "Number of bytes written (IO) until %date: %value"
    self.graphs.append(graph)

    # Memory usage graph.
    graph = StatGraph()
    series = dict()
    for stat_entry in stats:
      series[int(stat_entry.age/1e3)] = int(stat_entry.RSS_size)
    data = [[k, series[k]] for k in sorted(series)]
    graph.series = [StatData("RSS size", ",".join(map(str, data)))]

    series = dict()
    for stat_entry in stats:
      series[int(stat_entry.age/1e3)] = int(stat_entry.VMS_size)
    data = [[k, series[k]] for k in sorted(series)]
    graph.series.append(StatData("VMS size", ",".join(map(str, data))))

    graph.name = "Memory Usage"
    graph.id = "memory"
    graph.click_text = "Memory usage on %date: %value"
    self.graphs.append(graph)

    # Network traffic graphs.
    graph = StatGraph()
    series = dict()
    for stat_entry in stats:
      series[int(stat_entry.age/1e3)] = int(stat_entry.bytes_received)
    data = [[k, series[k]] for k in sorted(series)]
    graph.series = [StatData("Network Bytes Received",
                             ",".join(map(str, data)))]
    graph.name = "Network Bytes Received"
    graph.id = "nw_received"
    graph.click_text = "Network bytes received until %date: %value"
    self.graphs.append(graph)

    graph = StatGraph()
    series = dict()
    for stat_entry in stats:
      series[int(stat_entry.age/1e3)] = int(stat_entry.bytes_sent)
    data = [[k, series[k]] for k in sorted(series)]
    graph.series = [StatData("Network Bytes Sent", ",".join(map(str, data)))]
    graph.name = "Network Bytes Sent"
    graph.id = "nw_sent"
    graph.click_text = "Network bytes sent until %date: %value"
    self.graphs.append(graph)

    return super(AFF4ClientStats, self).Layout(request, response)

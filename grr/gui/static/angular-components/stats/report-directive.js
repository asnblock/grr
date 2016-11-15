'use strict';

goog.provide('grrUi.stats.reportDirective.ReportController');
goog.provide('grrUi.stats.reportDirective.ReportDirective');

goog.require('grrUi.core.apiService.stripTypeInfo');
goog.require('grrUi.core.utils.upperCaseToTitleCase');

goog.scope(function() {

var stripTypeInfo = grrUi.core.apiService.stripTypeInfo;

/** @type {number} */
var MONTH_SECONDS = 30*24*60*60;

// A month ago
/** @type {number} */
var DEFAULT_START_TIME = (moment().valueOf() - MONTH_SECONDS * 1000) * 1000;

// One month
/** @type {number} */
var DEFAULT_DURATION = MONTH_SECONDS;

/** @type {string} */
var DEFAULT_CLIENT_LABEL = '';

/**
 * Controller for ReportDirective.
 *
 * @param {!angular.Scope} $scope
 * @param {!grrUi.core.apiService.ApiService} grrApiService
 * @param {!grrUi.core.reflectionService.ReflectionService} grrReflectionService
 * @constructor
 * @ngInject
 */
grrUi.stats.reportDirective.ReportController =
    function($scope, grrApiService, grrReflectionService) {
  /** @private {!angular.Scope} */
  this.scope_ = $scope;

  /** @private {!grrUi.core.apiService.ApiService} */
  this.grrApiService_ = grrApiService;

  /** @private {!grrUi.core.reflectionService.ReflectionService} */
  this.grrReflectionService_ = grrReflectionService;

  /** @type {string}
   * This is intended to be an enum with the following possible values:
   * 'INITIAL' -- Select a report
   * 'LOADING' -- Loading...
   * 'LOADED' -- Selectors and a chart
   */
  this.state = 'INITIAL';

  /** @type {string} */
  this.titleCasedType;

  /** @type {*} */
  this.reportData;

  /** @type {*} */
  this.reportDesc;

  /** @type {number} */
  this.startTime = DEFAULT_START_TIME;

  /** @type {Object} */
  this.typedStartTime;

  /** @type {number} */
  this.duration = DEFAULT_DURATION;

  /** @type {Object} */
  this.typedDuration;

  /** @type {*} */
  this.labelsList;

  /** @type {string} */
  this.clientLabel = DEFAULT_CLIENT_LABEL;

  /** @type {string} */
  this.modelClientLabel = DEFAULT_CLIENT_LABEL;

  this.scope_.$watchGroup(['name', 'startTime', 'duration', 'clientLabel'],
                          this.onParamsChange_.bind(this));

  // TODO(user): Abstract the timerange selection logic to a different
  //                directive.
  this.grrReflectionService_.getRDFValueDescriptor('RDFDatetime').then(
      function(rdfDesc) {
    this.typedStartTime = angular.copy(rdfDesc['default']);
    this.typedStartTime['value'] = this.startTime;

    this.scope_.$watch('controller.startTime', function() {
        this.typedStartTime['value'] = this.startTime;
    }.bind(this));

    return this.grrReflectionService_.getRDFValueDescriptor('Duration');
  }.bind(this)).then(function(rdfDesc) {
    this.typedDuration = angular.copy(rdfDesc['default']);
    this.typedDuration['value'] = this.duration;

    this.scope_.$watch('controller.duration', function() {
        this.typedDuration['value'] = this.duration;
    }.bind(this));
  }.bind(this));


  // TODO(user): Labels selector should be abstracted into a separate
  //                component. When that's done it should also be reused
  //                in foreman-label-rule-form-directive.js .
  this.grrApiService_.get('/clients/labels').then(function(response) {
    this.labelsList = stripTypeInfo(response['data']['items']);
  }.bind(this));

  this.scope_.$watch('controller.clientLabel', function() {
      this.modelClientLabel = this.clientLabel;
  }.bind(this));
};
var ReportController =
    grrUi.stats.reportDirective.ReportController;


/**
 * Handles changes to the scope parameters.
 *
 * @private
 */
ReportController.prototype.onParamsChange_ = function() {
  var startTime = this.scope_['startTime'];
  if (startTime) {
    this.startTime = startTime;
  }
  if (startTime === null) {
    this.startTime = DEFAULT_START_TIME;
  }

  var duration = this.scope_['duration'];
  if (duration) {
    this.duration = duration;
  }
  if (duration === null) {
    this.duration = DEFAULT_DURATION;
  }

  var clientLabel = this.scope_['clientLabel'];
  if (clientLabel) {
    this.clientLabel = clientLabel;
  }
  if (clientLabel === null) {
    this.clientLabel = DEFAULT_CLIENT_LABEL;
  }

  if (this.scope_['name']) {
    this.fetchData_();
  }
};

/**
 * Handles "Show report" button clicks. Refreshes the report.
 */
ReportController.prototype.refreshReport = function() {
  // If the values are different than before, this triggers onParamsChange_
  // which triggers fetchData_.
  this.scope_['startTime'] = this.typedStartTime['value'];
  this.scope_['duration'] = this.typedDuration['value'];
  this.scope_['clientLabel'] = this.modelClientLabel;
};

/**
 * Fetches data from the api call.
 *
 * @private
 */
ReportController.prototype.fetchData_ = function() {
  var name = this.scope_['name'];

  if (name) {
    this.state = 'LOADING';

    var apiUrl = 'stats/reports/' + name;
    var apiParams = {
      start_time: this.startTime,
      duration: this.duration,
      client_label: this.clientLabel
    };

    this.grrApiService_.get(apiUrl, apiParams).then(function(response) {
      this.reportData = stripTypeInfo(response['data']['data']);
      this.reportDesc = stripTypeInfo(response['data']['desc']);

      this.titleCasedType =
          grrUi.core.utils.upperCaseToTitleCase(this.reportDesc['type']);

      this.state = 'LOADED';
    }.bind(this));
  }
};

/**
 * ReportDirective definition.
 *
 * @return {angular.Directive} Directive definition object.
 */
grrUi.stats.reportDirective.ReportDirective = function() {
  return {
    scope: {
      name: '=?',
      startTime: '=?',
      duration: '=?',
      clientLabel: '=?'
    },
    restrict: 'E',
    templateUrl: '/static/angular-components/stats/report.html',
    controller: ReportController,
    controllerAs: 'controller'
  };
};


/**
 * Name of the directive in Angular.
 *
 * @const
 * @export
 */
grrUi.stats.reportDirective.ReportDirective.directive_name =
    'grrReport';

});  // goog.scope

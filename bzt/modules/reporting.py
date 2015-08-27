"""
Basics of reporting capabilities

Copyright 2015 BlazeMeter Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import copy
import csv
import os
import time
from datetime import datetime

from bzt.modules.aggregator import DataPoint, KPISet
from bzt.engine import Reporter, AggregatorListener
from bzt.modules.passfail import PassFailStatus
from bzt.modules.blazemeter import BlazeMeterUploader
from bzt.six import etree, iteritems, string_types


class FinalStatus(Reporter, AggregatorListener):
    """
    A reporter that prints short statistics on test end
    """

    def __init__(self):
        super(FinalStatus, self).__init__()
        self.last_sec = None
        self.start_time = time.time()
        self.end_time = None

    def startup(self):
        self.start_time = time.time()

    def aggregated_second(self, data):
        """
        Just store the latest info

        :type data: bzt.modules.aggregator.DataPoint
        """
        self.last_sec = data

    def post_process(self):
        """
        Log basic stats
        """
        super(FinalStatus, self).post_process()

        self.end_time = time.time()

        if self.parameters.get("test-duration", True):
            self.__report_duration()

        if self.last_sec:
            summary_kpi = self.last_sec[DataPoint.CUMULATIVE][""]

            if self.parameters.get("summary", True):
                self.__report_samples_count(summary_kpi)
            if self.parameters.get("percentiles", True):
                self.__report_percentiles(summary_kpi)

            if self.parameters.get("failed-labels", False):
                self.__report_failed_labels(self.last_sec[DataPoint.CUMULATIVE])

            if self.parameters.get("dump-xml", None):
                self.__dump_xml(self.parameters.get("dump-xml"))

            if self.parameters.get("dump-csv", None):
                self.__dump_csv(self.parameters.get("dump-csv"))

    def __report_samples_count(self, summary_kpi_set):
        """
        reports samples count
        """
        err_rate = 100 * summary_kpi_set[KPISet.FAILURES] / float(summary_kpi_set[KPISet.SAMPLE_COUNT])
        self.log.info("Samples count: %s, %.2f%% failures", summary_kpi_set[KPISet.SAMPLE_COUNT], err_rate)

    def __report_percentiles(self, summary_kpi_set):
        """
        reports percentiles
        """

        fmt = "Average times: total %.3f, latency %.3f, connect %.3f"
        self.log.info(fmt, summary_kpi_set[KPISet.AVG_RESP_TIME], summary_kpi_set[KPISet.AVG_LATENCY],
                      summary_kpi_set[KPISet.AVG_CONN_TIME])

        for key in sorted(summary_kpi_set[KPISet.PERCENTILES].keys(), key=float):
            self.log.info("Percentile %.1f%%: %.3f", float(key), summary_kpi_set[KPISet.PERCENTILES][key])

    def __report_failed_labels(self, cumulative):
        """
        reports failed labels
        """
        report_template = "%d failed samples: %s"
        sorted_labels = sorted(cumulative.keys())
        for sample_label in sorted_labels:
            if sample_label != "":
                failed_samples_count = cumulative[sample_label]['fail']
                if failed_samples_count:
                    self.log.info(report_template, failed_samples_count, sample_label)

    def __report_duration(self):
        """
        asks executors start_time and end_time, provides time delta
        """

        date_start = datetime.fromtimestamp(int(self.start_time))
        date_end = datetime.fromtimestamp(int(self.end_time))
        self.log.info("Test duration: %s", date_end - date_start)

    def __dump_xml(self, filename):
        self.log.info("Dumping final status as XML: %s", filename)
        root = etree.Element("FinalStatus")
        if self.last_sec:
            for label, kpiset in iteritems(self.last_sec[DataPoint.CUMULATIVE]):
                root.append(self.__get_xml_summary(label, kpiset))

        with open(filename, 'wb') as fhd:
            tree = etree.ElementTree(root)
            tree.write(fhd, pretty_print=True, encoding="UTF-8", xml_declaration=True)

    def __get_xml_summary(self, label, kpiset):
        elem = etree.Element("Group", label=label)
        for kpi_name, kpi_val in iteritems(kpiset):
            if kpi_name in ('errors', 'rt'):
                continue

            if isinstance(kpi_val, dict):
                for param_name, param_val in iteritems(kpi_val):
                    elem.append(self.__get_kpi_xml(kpi_name, param_val, param_name))
            else:
                elem.append(self.__get_kpi_xml(kpi_name, kpi_val))

        return elem

    def __get_kpi_xml(self, kpi_name, kpi_val, param=None):
        kpi = etree.Element(kpi_name)
        kpi.attrib['value'] = self.__val_to_str(kpi_val)
        elm_name = etree.Element("name")
        elm_name.text = kpi_name
        if param is not None:
            kpi.attrib['param'] = self.__val_to_str(param)
            elm_name.text += "/" + param

        kpi.append(elm_name)

        elm_value = etree.Element("value")
        elm_value.text = self.__val_to_str(kpi_val)
        kpi.append(elm_value)

        return kpi

    def __val_to_str(self, kpi_val):
        if isinstance(kpi_val, float):
            return '%.5f' % kpi_val
        elif isinstance(kpi_val, int):
            return '%d' % kpi_val
        elif isinstance(kpi_val, string_types):
            return kpi_val
        else:
            raise ValueError("Unhandled kpi type: %s" % type(kpi_val))

    def __dump_csv(self, filename):
        self.log.info("Dumping final status as CSV: %s", filename)
        # FIXME: what if there's no last_sec
        with open(filename, 'wb') as fhd:
            writer = csv.DictWriter(fhd, self.__get_csv_dict('', self.last_sec[DataPoint.CUMULATIVE]['']).keys())
            writer.writeheader()
            for label, kpiset in iteritems(self.last_sec[DataPoint.CUMULATIVE]):
                writer.writerow(self.__get_csv_dict(label, kpiset))

    def __get_csv_dict(self, label, kpiset):
        res = copy.deepcopy(kpiset)
        for level, val in iteritems(kpiset[KPISet.PERCENTILES]):
            res['perc_%s' % level] = val

        for rc, val in iteritems(kpiset[KPISet.RESP_CODES]):
            res['rc_%s' % rc] = val

        for key in res:
            if isinstance(res[key], float):
                res[key] = "%.5f" % res[key]

        del res['errors']
        del res['rt']
        del res['rc']
        del res['perc']
        res['label'] = label
        return res


class JUnitXMLReporter(Reporter, AggregatorListener):
    """
    A reporter that exports results in Jenkins JUnit XML format.
    """

    REPORT_FILE_NAME = "xunit"
    REPORT_FILE_EXT = ".xml"

    def __init__(self):
        super(JUnitXMLReporter, self).__init__()
        self.report_file_path = "xunit.xml"
        self.last_second = None

    def prepare(self):
        """
        create artifacts, parse options.
        report filename from parameters
        :return:
        """

        filename = self.parameters.get("filename", None)
        if filename:
            self.report_file_path = filename
        else:
            self.report_file_path = self.engine.create_artifact(JUnitXMLReporter.REPORT_FILE_NAME,
                                                                JUnitXMLReporter.REPORT_FILE_EXT)
        self.parameters["filename"] = self.report_file_path

    def aggregated_second(self, data):
        """
        :param data:
        :return:
        """
        self.last_second = data

    def post_process(self):
        """
        Get report data, generate xml report.
        """
        super(JUnitXMLReporter, self).post_process()
        test_data_source = self.parameters.get("data-source", "sample-labels")

        if self.last_second:
            # data-source sample-labels
            if test_data_source == "sample-labels":
                root_element = self.process_sample_labels()
            # data-source pass-fail
            elif test_data_source == "pass-fail":
                root_element = self.process_pass_fail()
            else:
                raise ValueError("Unsupported data source: %s" % test_data_source)

            self.save_report(root_element)

    def process_sample_labels(self):
        """
        :return: etree element
        """
        _kpiset = self.last_second[DataPoint.CUMULATIVE]
        root_xml_element = etree.Element("testsuite", name="sample_labels", package="bzt")
        bza_report_info = self.get_bza_report_info()
        class_name = bza_report_info[0][1] if bza_report_info else "bzt-" + str(self.__hash__())
        report_urls = [info_item[0] for info_item in bza_report_info]

        for key in sorted(_kpiset.keys()):
            if key == "":  # if label is not blank
                continue

            test_case_etree_element = etree.Element("testcase", classname=class_name, name=key)
            if report_urls:
                system_out_etree = etree.SubElement(test_case_etree_element, "system-out")
                system_out_etree.text = "".join(report_urls)
            if _kpiset[key][KPISet.ERRORS]:
                for er_dict in _kpiset[key][KPISet.ERRORS]:
                    err_message = str(er_dict["rc"])
                    err_type = str(er_dict["msg"])
                    err_desc = "total errors of this type:" + str(er_dict["cnt"])
                    err_etree_element = etree.Element("error", message=err_message, type=err_type)
                    err_etree_element.text = err_desc
                    test_case_etree_element.append(err_etree_element)

            root_xml_element.append(test_case_etree_element)

        return root_xml_element

    def get_bza_report_info(self):
        """
        :return: [(url, test), (url, test), ...]
        """
        result = []
        bza_reporters = [_x for _x in self.engine.reporters if isinstance(_x, BlazeMeterUploader)]
        for bza_reporter in bza_reporters:
            report_url = None
            test_name = None

            if bza_reporter.client.results_url:
                report_url = "BlazeMeter report link: %s\n" % bza_reporter.client.results_url
            if bza_reporter.client.test_id:
                test_name = bza_reporter.parameters.get("test", None)
            result.append((report_url, test_name))

        if len(result) > 1:
            self.log.warning("More then one blazemeter reporter found")
        return result

    def save_report(self, root_node):
        """
        :param root_node:
        :return:
        """
        try:
            if os.path.exists(self.report_file_path):
                self.log.warning("File %s already exists, will be overwritten", self.report_file_path)
            else:
                dirname = os.path.dirname(self.report_file_path)
                if dirname and not os.path.exists(dirname):
                    os.makedirs(dirname)

            etree_obj = etree.ElementTree(root_node)
            self.log.info("Writing JUnit XML report into: %s", self.report_file_path)
            with open(self.report_file_path, 'wb') as _fds:
                etree_obj.write(_fds, xml_declaration=True, encoding="UTF-8", pretty_print=True)

        except BaseException:
            self.log.error("Cannot create file %s", self.report_file_path)
            raise

    def process_pass_fail(self):
        """
        :return: etree element
        """
        pass_fail_objects = [_x for _x in self.engine.reporters if isinstance(_x, PassFailStatus)]
        fail_criterias = []
        for pf_obj in pass_fail_objects:
            if pf_obj.criterias:
                for _fc in pf_obj.criterias:
                    fail_criterias.append(_fc)
        root_xml_element = etree.Element("testsuite", name='bzt_pass_fail', package="bzt")

        bza_report_info = self.get_bza_report_info()
        classname = bza_report_info[0][1] if bza_report_info else "bzt-" + str(self.__hash__())
        report_urls = [info_item[0] for info_item in bza_report_info]

        for fc_obj in fail_criterias:
            if fc_obj.config['label']:
                data = (fc_obj.config['subject'], fc_obj.config['label'],
                        fc_obj.config['condition'], fc_obj.config['threshold'])
                tpl = "%s of %s%s%s"
            else:
                data = (fc_obj.config['subject'], fc_obj.config['condition'],
                        fc_obj.config['threshold'])
                tpl = "%s%s%s"

            if fc_obj.config['timeframe']:
                tpl += " for %s"
                data += (fc_obj.config['timeframe'],)

            disp_name = tpl % data

            testcase_etree = etree.Element("testcase", classname=classname, name=disp_name)
            if report_urls:
                system_out_etree = etree.SubElement(testcase_etree, "system-out")
                system_out_etree.text = "".join(report_urls)
            if fc_obj.is_triggered and fc_obj.fail:
                etree.SubElement(testcase_etree, "error", type="pass/fail criteria triggered", message="")
            root_xml_element.append(testcase_etree)
        return root_xml_element

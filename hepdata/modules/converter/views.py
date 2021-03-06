#
# This file is part of HEPData.
# Copyright (C) 2016 CERN.
#
# HEPData is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# HEPData is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HEPData; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.
#
from __future__ import absolute_import, print_function
import logging
import os

from celery import shared_task
from flask import Blueprint, send_file, render_template, \
    request, current_app, redirect
import time
from werkzeug.utils import secure_filename
from hepdata.config import CFG_CONVERTER_URL, CFG_SUPPORTED_FORMATS

from hepdata_converter_ws_client import convert
from hepdata.modules.converter import convert_zip_archive
from hepdata.modules.submission.api import get_latest_hepsubmission
from hepdata.modules.submission.models import HEPSubmission, DataResource, DataSubmission
from hepdata.utils.file_extractor import extract, get_file_in_directory
from hepdata.modules.records.utils.common import get_record_contents

logging.basicConfig()
log = logging.getLogger(__name__)

blueprint = Blueprint('converter', __name__,
                      url_prefix="/download",
                      template_folder='templates',
                      static_folder='static')


@blueprint.route('/convert', methods=['GET', 'POST'])
def convert_endpoint():
    """ Endpoint for general conversion, the file is passed as a GET parameter
     and options ('from=' & 'to=') are query string arguments. """

    input_format, output_format = request.args.get('from', 'oldhepdata'), request.args.get('to', 'yaml')

    if input_format not in ['yaml', 'oldhepdata'] or \
            output_format not in ['root', 'yoda', 'csv', 'yaml']:
        return display_error(
            title="Chosen formats are not supported",
            description="Supported input formats: oldhepdata, yaml\n" +
                        "Supported output formats: root, yoda, csv"
        )

    fileobject = request.files.get('file')
    if not fileobject or not fileobject.filename.endswith('.zip'):
        print("Fileobject: " + str(fileobject))
        return display_error(
            title="Please send a zip file for conversion",
            description="No file has been sent or it does not have a zip extension"
        )

    filename = secure_filename(fileobject.filename)
    timestamp = str(int(round(time.time())))
    input_archive = os.path.join(current_app.config['CFG_TMPDIR'], timestamp + '.zip')
    fileobject.save(input_archive)

    options = {
        'input_format': input_format,
        'output_format': output_format,
        'filename': filename[:-4],
    }
    output_file = os.path.join(current_app.config['CFG_TMPDIR'], timestamp + '.tar.gz')
    conversion_result = convert_zip_archive(input_archive, output_file, options)

    os.remove(input_archive)

    if not conversion_result:
        return display_error(
            title="Your archive does not contain necessary files",
            description="For YAML conversions a submission.yaml file is necessary,"
                        " and for conversions from the oldhepdata format"
                        " a file with .oldhepdata extension is required."
        )

    return send_file(conversion_result, as_attachment=True)


@blueprint.route('/submission/<string:inspire_id>/<int:version>/<string:file_format>')
@blueprint.route('/submission/<string:inspire_id>/<string:file_format>')
def download_submission_with_inspire_id(*args, **kwargs):
    """
       Gets the submission file and either serves it back directly from YAML, or converts it
       for other formats.

       :param inspire_id: inspire id
       :param version: version of submission to export. If absent, returns the latest.
       :param file_format: yaml, csv, root, or yoda
       :return:
    """

    inspire_id = kwargs.pop('inspire_id')

    if 'ins' in inspire_id:
        inspire_id = inspire_id.replace('ins', '')

    if 'version' in kwargs:
        submission = HEPSubmission.query.filter_by(inspire_id=inspire_id, version=kwargs.pop('version')).first()
    else:
        submission = get_latest_hepsubmission(inspire_id=inspire_id)

    if not submission:
        return display_error(
            title="No submission found",
            description="A submission with Inspire ID {0} does not exist".format(inspire_id)
        )

    return download_submission(submission, kwargs.pop('file_format'))


@blueprint.route('/submission/<int:recid>/<int:version>/<string:file_format>')
@blueprint.route('/submission/<int:recid>/<string:file_format>')
def download_submission_with_recid(*args, **kwargs):
    """
        Gets the submission file and either serves it back directly from YAML, or converts it
        for other formats.

        :param recid: submissions recid
        :param version: version of submission to export. If absent, returns the latest.
        :param file_format: yaml, csv, root, or yoda
        :return:
    """
    recid = kwargs.pop('recid')
    if 'version' in kwargs:
        submission = HEPSubmission.query.filter_by(publication_recid=recid, version=kwargs.pop('version')) \
            .first()
    else:
        submission = get_latest_hepsubmission(publication_recid=recid)

    if not submission:
        return display_error(
            title="No submission found",
            description="A submission with record ID {0} does not exist".format(recid)
        )

    return download_submission(submission, kwargs.pop('file_format'))


@shared_task()
def download_submission(submission, file_format, offline=False, force=False):
    """
    Gets the submission file and either serves it back directly from YAML, or converts it
    for other formats.

    :param submission: HEPSubmission
    :param file_format: yaml, csv, root, or yoda
    :return:
    """

    version = submission.version

    file_identifier = submission.publication_recid
    if submission.inspire_id:
        file_identifier = 'ins{0}'.format(submission.inspire_id)

    if file_format == 'json':
        return redirect('/record/{0}?version={1}&format=json'.format(file_identifier, version))
    elif file_format not in CFG_SUPPORTED_FORMATS:
        if offline:
            log.error('Format not supported')
        return display_error(
            title="The " + file_format + " output format is not supported",
            description="This output format is not supported. " +
                        "Currently supported formats: " + str(CFG_SUPPORTED_FORMATS),
        )

    path = os.path.join(current_app.config['CFG_DATADIR'], str(submission.publication_recid))
    data_filename = current_app.config['SUBMISSION_FILE_NAME_PATTERN'].format(submission.publication_recid, version)

    output_file = 'HEPData-{0}-v{1}-{2}.tar.gz'.format(file_identifier, submission.version, file_format)

    converted_dir = os.path.join(current_app.config['CFG_DATADIR'], 'converted')
    if not os.path.exists(converted_dir):
        os.mkdir(converted_dir)

    output_path = os.path.join(converted_dir, output_file)

    # If the file is already available in the dir, send it back
    # unless we are forcing recreation of the file or the submission is not finished.
    if os.path.exists(output_path) and not force and submission.overall_status == 'finished':
        if not offline:
            return send_file(
                output_path,
                as_attachment=True,
            )
        else:
            print('File already downloaded at {0}'.format(output_path))
            return

    converter_options = {
        'input_format': 'yaml',
        'output_format': file_format,
        'filename': 'HEPData-{0}-v{1}-{2}'.format(file_identifier, submission.version, file_format),
    }

    if submission.doi:
        converter_options['hepdata_doi'] = '{0}.v{1}'.format(submission.doi, version)

    if submission.inspire_id and file_format == 'yoda':
        record = get_record_contents(submission.publication_recid)
        if record:
            converter_options['rivet_analysis_name'] = '{0}_{1}_I{2}'.format(
                ''.join(record['collaborations']).upper(), record['year'], submission.inspire_id)

    data_filepath = os.path.join(path, data_filename)

    converted_file = convert_zip_archive(data_filepath, output_path, converter_options)
    if not offline:
        return send_file(converted_file, as_attachment=True)
    else:
        print('File for {0} created successfully at {1}'.format(file_identifier, output_path))


@blueprint.route('/table/<string:inspire_id>/<string:table_name>/<int:version>/<string:file_format>')
@blueprint.route('/table/<string:inspire_id>/<string:table_name>/<string:file_format>')
def download_data_table_by_inspire_id(*args, **kwargs):
    """
    Downloads the latest data file given the url /download/submission/ins1283842/Table 1/yaml or
    by a particular version given /download/submission/ins1283842/Table 1/1/yaml

    :param args:
    :param kwargs: inspire_id, table_name, version (optional), and file_format
    :return:
    """
    inspire_id = kwargs.pop('inspire_id')
    table_name = kwargs.pop('table_name')

    if 'ins' in inspire_id:
        inspire_id = inspire_id.replace('ins', '')

    # Allow space in table_name to be omitted from URL.
    if ' ' not in table_name:
        table_name = table_name.replace('Table', 'Table ')

    if 'version' not in kwargs:
        version = get_latest_hepsubmission(inspire_id=inspire_id).version
    else:
        version = kwargs.pop('version')

    datasubmission = DataSubmission.query.filter_by(publication_inspire_id=inspire_id,
                                                    version=version, name=table_name).one()

    return download_datatable(datasubmission, kwargs.pop('file_format'),
                              submission_id='ins{0}'.format(inspire_id), table_name=table_name)


@blueprint.route('/table/<int:recid>/<string:table_name>/<int:version>/<string:file_format>')
@blueprint.route('/table/<int:recid>/<string:table_name>/<string:file_format>')
def download_data_table_by_recid(*args, **kwargs):
    """
    Record ID download
    Downloads the latest data file given the url /download/submission/1231/Table 1/yaml or
    by a particular version given /download/submission/1231/Table 1/1/yaml

    :param args:
    :param kwargs: inspire_id, table_name, version (optional), and file_format
    :return:
    """
    recid = kwargs.pop('recid')
    table_name = kwargs.pop('table_name')

    # Allow space in table_name to be omitted from URL.
    if ' ' not in table_name:
        table_name = table_name.replace('Table', 'Table ')

    if 'version' not in kwargs:
        version = get_latest_hepsubmission(publication_recid=recid).version
    else:
        version = kwargs.pop('version')

    datasubmission = DataSubmission.query.filter_by(publication_recid=recid, version=version, name=table_name).one()

    return download_datatable(datasubmission, kwargs.pop('file_format'),
                              submission_id='{0}'.format(recid), table_name=table_name)


@blueprint.route('/table/<int:data_id>/<string:file_format>')
def download_datatable_by_dataid(data_id, file_format):
    """ Download a particular data table in a given format. """

    datasubmission = DataSubmission.query.filter_by(id=data_id).one()

    return download_datatable(datasubmission, file_format, submission_id=data_id)


def download_datatable(datasubmission, file_format, *args, **kwargs):

    if file_format == 'json':
        return redirect('/record/data/{0}/{1}/{2}'.format(datasubmission.publication_recid,
                                                   datasubmission.id, datasubmission.version))
    elif file_format not in CFG_SUPPORTED_FORMATS:
        return display_error(
            title="The " + file_format + " output format is not supported",
            description="This output format is not supported. " +
                        "Currently supported formats: " + str(CFG_SUPPORTED_FORMATS),
        )

    dataresource = DataResource.query.filter_by(id=datasubmission.data_file).one()

    record_path, table_name = os.path.split(dataresource.file_location)

    filename = 'HEPData-{0}-v{1}'.format(kwargs.pop('submission_id'), datasubmission.version)
    if 'table_name' in kwargs:
        filename += '-' + kwargs.pop('table_name').replace(' ', '')

    output_path = os.path.join(current_app.config['CFG_TMPDIR'], filename)

    if file_format == 'yaml':
        return send_file(
            dataresource.file_location,
            as_attachment=True,
            attachment_filename=filename + '.yaml'
        )

    options = {
        'input_format': 'yaml',
        'output_format': file_format,
        'table': table_name,
        'filename': table_name.split('.')[0],
    }

    if datasubmission.doi:
        options['hepdata_doi'] = datasubmission.doi.rsplit('/', 1)[0].encode('ascii')

    if datasubmission.publication_inspire_id and file_format == 'yoda':
        record = get_record_contents(datasubmission.publication_recid)
        if record:
            options['rivet_analysis_name'] = '{0}_{1}_I{2}'.format(
                ''.join(record['collaborations']).upper(), record['year'], datasubmission.publication_inspire_id)

    if not os.path.exists(output_path):

        successful = convert(
            CFG_CONVERTER_URL,
            record_path,
            output=output_path + '-dir',
            options=options,
            extract=False,
        )
    else:
        successful = True

    # Error occurred, the output is a HTML file
    if successful:
        new_path = output_path + "." + file_format
        new_path = extract(filename + ".tar.gz", output_path + '-dir', new_path)
        file_to_send = get_file_in_directory(new_path, file_format)
    else:
        file_to_send = output_path + '-dir'
        file_format = 'html'

    return send_file(file_to_send, as_attachment=True,
                     attachment_filename=filename + '.' + file_format)


def display_error(title='Unknown Error', description=''):
    return render_template(
        'hepdata_records/error_page.html',
        message=title,
        errors={
            "Converter": [{
                "level": "error",
                "message": description
            }]
        }
    )

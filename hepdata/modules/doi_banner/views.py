from flask import Blueprint, redirect, abort, send_file, url_for
from hepdata.ext.elasticsearch.api import get_records_matching_field
import logging
import os

logging.basicConfig()
log = logging.getLogger(__name__)

blueprint = Blueprint('doi_banner', __name__,
                      url_prefix="",
                      static_folder='static')

base_dir = os.path.dirname(os.path.realpath(__file__))


@blueprint.route('/doidata/<path:doi>')
def resolve_doi_data(doi):
    """
    :param doi:
    :return:
    """
    matching = get_records_matching_field('doi', doi, source={"includes": ['inspire_id']})
    if matching.get('hits').get('total') > 0:
        _returned = matching.get('hits').get('hits')[0].get('_source').get('inspire_id')
        return redirect('/record/ins{0}'.format(_returned))
    return abort(404)


@blueprint.route('/doibanner/<path:doi>')
def get_doi_banner(doi):
    """

    :param doi:
    :return:
    """

    matching = get_records_matching_field('doi', doi, source={"includes": ['inspire_id']})
    if matching.get('hits').get('total') > 0:
        print(matching)
        return send_file(os.path.join(base_dir, 'static/img/hepdata-doi-banner.png'))
    else:
        return send_file(os.path.join(base_dir, 'static/img/1px.png'))

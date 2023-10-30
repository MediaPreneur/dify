import datetime
import logging
import time

import click
from celery import shared_task
from langchain.schema import Document
from werkzeug.exceptions import NotFound

from core.index.index import IndexBuilder
from extensions.ext_database import db
from extensions.ext_redis import redis_client
from models.dataset import DocumentSegment
from models.dataset import Document as DatasetDocument


@shared_task(queue='dataset')
def add_document_to_index_task(dataset_document_id: str):
    """
    Async Add document to index
    :param document_id:

    Usage: add_document_to_index.delay(document_id)
    """
    logging.info(
        click.style(
            f'Start add document to index: {dataset_document_id}', fg='green'
        )
    )
    start_at = time.perf_counter()

    dataset_document = db.session.query(DatasetDocument).filter(DatasetDocument.id == dataset_document_id).first()
    if not dataset_document:
        raise NotFound('Document not found')

    if dataset_document.indexing_status != 'completed':
        return

    indexing_cache_key = f'document_{dataset_document.id}_indexing'

    try:
        segments = db.session.query(DocumentSegment).filter(
            DocumentSegment.document_id == dataset_document.id,
            DocumentSegment.enabled == True
        ) \
            .order_by(DocumentSegment.position.asc()).all()

        documents = []
        for segment in segments:
            document = Document(
                page_content=segment.content,
                metadata={
                    "doc_id": segment.index_node_id,
                    "doc_hash": segment.index_node_hash,
                    "document_id": segment.document_id,
                    "dataset_id": segment.dataset_id,
                }
            )

            documents.append(document)

        dataset = dataset_document.dataset

        if not dataset:
            raise Exception('Document has no dataset')

        if index := IndexBuilder.get_index(dataset, 'high_quality'):
            index.add_texts(documents)

        if index := IndexBuilder.get_index(dataset, 'economy'):
            index.add_texts(documents)

        end_at = time.perf_counter()
        logging.info(
            click.style(
                f'Document added to index: {dataset_document.id} latency: {end_at - start_at}',
                fg='green',
            )
        )
    except Exception as e:
        logging.exception("add document to index failed")
        dataset_document.enabled = False
        dataset_document.disabled_at = datetime.datetime.utcnow()
        dataset_document.status = 'error'
        dataset_document.error = str(e)
        db.session.commit()
    finally:
        redis_client.delete(indexing_cache_key)

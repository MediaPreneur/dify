import datetime
import logging
import time
from typing import Optional, List

import click
from celery import shared_task
from langchain.schema import Document
from werkzeug.exceptions import NotFound

from core.index.index import IndexBuilder
from extensions.ext_database import db
from extensions.ext_redis import redis_client
from models.dataset import DocumentSegment


@shared_task(queue='dataset')
def create_segment_to_index_task(segment_id: str, keywords: Optional[List[str]] = None):
    """
    Async create segment to index
    :param segment_id:
    :param keywords:
    Usage: create_segment_to_index_task.delay(segment_id)
    """
    logging.info(
        click.style(f'Start create segment to index: {segment_id}', fg='green')
    )
    start_at = time.perf_counter()

    segment = db.session.query(DocumentSegment).filter(DocumentSegment.id == segment_id).first()
    if not segment:
        raise NotFound('Segment not found')

    if segment.status != 'waiting':
        return

    indexing_cache_key = f'segment_{segment.id}_indexing'

    try:
        # update segment status to indexing
        update_params = {
            DocumentSegment.status: "indexing",
            DocumentSegment.indexing_at: datetime.datetime.utcnow()
        }
        DocumentSegment.query.filter_by(id=segment.id).update(update_params)
        db.session.commit()
        document = Document(
            page_content=segment.content,
            metadata={
                "doc_id": segment.index_node_id,
                "doc_hash": segment.index_node_hash,
                "document_id": segment.document_id,
                "dataset_id": segment.dataset_id,
            }
        )

        dataset = segment.dataset

        if not dataset:
            logging.info(
                click.style(
                    f'Segment {segment.id} has no dataset, pass.', fg='cyan'
                )
            )
            return

        dataset_document = segment.document

        if not dataset_document:
            logging.info(
                click.style(
                    f'Segment {segment.id} has no document, pass.', fg='cyan'
                )
            )
            return

        if not dataset_document.enabled or dataset_document.archived or dataset_document.indexing_status != 'completed':
            logging.info(
                click.style(
                    f'Segment {segment.id} document status is invalid, pass.',
                    fg='cyan',
                )
            )
            return

        if index := IndexBuilder.get_index(dataset, 'high_quality'):
            index.add_texts([document], duplicate_check=True)

        if index := IndexBuilder.get_index(dataset, 'economy'):
            if keywords and len(keywords) > 0:
                index.create_segment_keywords(segment.index_node_id, keywords)
            else:
                index.add_texts([document])

        # update segment to completed
        update_params = {
            DocumentSegment.status: "completed",
            DocumentSegment.completed_at: datetime.datetime.utcnow()
        }
        DocumentSegment.query.filter_by(id=segment.id).update(update_params)
        db.session.commit()

        end_at = time.perf_counter()
        logging.info(
            click.style(
                f'Segment created to index: {segment.id} latency: {end_at - start_at}',
                fg='green',
            )
        )
    except Exception as e:
        logging.exception("create segment to index failed")
        segment.enabled = False
        segment.disabled_at = datetime.datetime.utcnow()
        segment.status = 'error'
        segment.error = str(e)
        db.session.commit()
    finally:
        redis_client.delete(indexing_cache_key)

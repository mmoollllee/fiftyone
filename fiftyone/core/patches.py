"""
Patches views.

| Copyright 2017-2021, Voxel51, Inc.
| `voxel51.com <https://voxel51.com/>`_
|
"""
from copy import deepcopy

import eta.core.utils as etau

import fiftyone.core.aggregations as foa
import fiftyone.core.labels as fol
import fiftyone.core.sample as fos
import fiftyone.core.view as fov


class _PatchView(fos.SampleView):
    def save(self):
        super().save()

        # Update source collection
        self._view._sync_source_sample(self)


class PatchView(_PatchView):
    """A patch in a :class:`PatchesView`.

    :class:`PatchView` instances should not be created manually; they are
    generated by iterating over :class:`PatchesView` instances.

    Args:
        doc: a :class:`fiftyone.core.odm.DatasetSampleDocument`
        view: the :class:`PatchesView` that the patch belongs to
        selected_fields (None): a set of field names that this view is
            restricted to
        excluded_fields (None): a set of field names that are excluded from
            this view
        filtered_fields (None): a set of field names of list fields that are
            filtered in this view
    """

    pass


class EvaluationPatchView(_PatchView):
    """A patch in an :class:`EvaluationPatchesView`.

    :class:`EvaluationPatchView` instances should not be created manually; they
    are generated by iterating over :class:`EvaluationPatchesView` instances.

    Args:
        doc: a :class:`fiftyone.core.odm.DatasetSampleDocument`
        view: the :class:`EvaluationPatchesView` that the patch belongs to
        selected_fields (None): a set of field names that this view is
            restricted to
        excluded_fields (None): a set of field names that are excluded from
            this view
        filtered_fields (None): a set of field names of list fields that are
            filtered in this view
    """

    pass


class _PatchesView(fov.DatasetView):
    def __init__(
        self, source_collection, patches_stage, patches_dataset, _stages=None
    ):
        if _stages is None:
            _stages = []

        self._source_collection = source_collection
        self._patches_stage = patches_stage
        self._patches_dataset = patches_dataset
        self.__stages = _stages

    def __copy__(self):
        return self.__class__(
            self._source_collection,
            deepcopy(self._patches_stage),
            self._patches_dataset,
            _stages=deepcopy(self.__stages),
        )

    @property
    def _label_fields(self):
        raise NotImplementedError("subclass must implement _label_fields")

    @property
    def _dataset(self):
        return self._patches_dataset

    @property
    def _root_dataset(self):
        return self._source_collection._root_dataset

    @property
    def _stages(self):
        return self.__stages

    @property
    def _all_stages(self):
        return (
            self._source_collection.view()._all_stages
            + [self._patches_stage]
            + self.__stages
        )

    @property
    def _element_str(self):
        return "patch"

    @property
    def _elements_str(self):
        return "patches"

    @property
    def name(self):
        return self.dataset_name + "-patches"

    def _edit_label_tags(self, edit_fcn, label_fields=None):
        # This covers the necessary overrides for both `tag_labels()` and
        # `untag_labels()`

        if etau.is_str(label_fields):
            label_fields = [label_fields]

        super()._edit_label_tags(edit_fcn, label_fields=label_fields)

        # Update source collection

        if label_fields is None:
            fields = self._label_fields
        else:
            fields = [l for l in label_fields if l in self._label_fields]

        def sync_fcn(view, field):
            view._edit_label_tags(edit_fcn, label_fields=[field])

        self._sync_source_fcn(sync_fcn, fields)

    def set_values(self, field_name, *args, **kwargs):
        super().set_values(field_name, *args, **kwargs)

        # Update source collection

        field = field_name.split(".", 1)[0]
        if field in self._label_fields:
            self._sync_source_view_field(field)

    def save(self, fields=None):
        if etau.is_str(fields):
            fields = [fields]

        super().save(fields=fields)

        # Update source collection

        if fields is None:
            fields = self._label_fields
        else:
            fields = [l for l in fields if l in self._label_fields]

        #
        # IMPORTANT: we sync the contents of `_patches_dataset`, not `self`
        # here because the `save()` call above updated the dataset, which means
        # this view may no longer have the same contents (e.g., if `skip()` is
        # involved)
        #

        self._sync_source_root(fields)

    def reload(self):
        self._root_dataset.reload()

        #
        # Regenerate the patches dataset
        #
        # This assumes that calling `load_view()` when the current patches
        # dataset has been deleted will cause a new one to be generated
        #

        self._patches_dataset.delete()
        _view = self._patches_stage.load_view(self._source_collection)
        self._patches_dataset = _view._patches_dataset

    def _sync_source_sample(self, sample):
        for field in self._label_fields:
            self._sync_source_sample_field(sample, field)

    def _sync_source_sample_field(self, sample, field):
        label_type = self._patches_dataset._get_label_field_type(field)
        is_list_field = issubclass(label_type, fol._LABEL_LIST_FIELDS)

        doc = sample._doc.field_to_mongo(field)
        if is_list_field:
            doc = doc[label_type._LABEL_LIST_FIELD]

        self._source_collection._set_labels_by_id(
            field, [sample.sample_id], [doc]
        )

    def _sync_source_fcn(self, sync_fcn, fields):
        for field in fields:
            _, id_path = self._get_label_field_path(field, "id")
            ids = self.values(id_path, unwind=True)
            source_view = self._source_collection.select_labels(
                ids=ids, fields=field
            )
            sync_fcn(source_view, field)

    def _sync_source_root(self, fields):
        for field in fields:
            self._sync_source_root_field(field)

    def _sync_source_view_field(self, field):
        _, label_path = self._get_label_field_path(field)

        sample_ids, docs = self.aggregate(
            [foa.Values("sample_id"), foa.Values(label_path, _raw=True)]
        )

        self._source_collection._set_labels_by_id(field, sample_ids, docs)

    def _sync_source_root_field(self, field):
        _, id_path = self._get_label_field_path(field, "id")
        label_path = id_path.rsplit(".", 1)[0]

        #
        # Sync label updates
        #

        sample_ids, docs, label_ids = self._patches_dataset.aggregate(
            [
                foa.Values("sample_id"),
                foa.Values(label_path, _raw=True),
                foa.Values(id_path, unwind=True),
            ]
        )

        self._source_collection._set_labels_by_id(field, sample_ids, docs)

        #
        # Sync label deletions
        #

        _, src_id_path = self._source_collection._get_label_field_path(
            field, "id"
        )
        src_ids = self._source_collection.values(src_id_path, unwind=True)
        delete_ids = set(src_ids) - set(label_ids)

        if delete_ids:
            self._source_collection._dataset.delete_labels(
                ids=delete_ids, fields=field
            )

    def _get_ids_map(self, field):
        label_type = self._patches_dataset._get_label_field_type(field)
        is_list_field = issubclass(label_type, fol._LABEL_LIST_FIELDS)

        _, id_path = self._get_label_field_path(field, "id")

        sample_ids, label_ids = self.aggregate(
            [foa.Values("id"), foa.Values(id_path)]
        )

        ids_map = {}
        if is_list_field:
            for sample_id, _label_ids in zip(sample_ids, label_ids):
                if not _label_ids:
                    continue

                for label_id in _label_ids:
                    ids_map[label_id] = sample_id

        else:
            for sample_id, label_id in zip(sample_ids, label_ids):
                if not label_id:
                    continue

                ids_map[label_id] = sample_id

        return ids_map


class PatchesView(_PatchesView):
    """A :class:`fiftyone.core.view.DatasetView` of patches from a
    :class:`fiftyone.core.dataset.Dataset`.

    Patches views contain an ordered collection of patch samples, each of which
    contains a subset of a sample of the parent dataset corresponding to a
    single object or logical grouping of of objects.

    Patches retrieved from patches views are returned as :class:`PatchView`
    objects.

    Args:
        source_collection: the
            :class:`fiftyone.core.collections.SampleCollection` from which this
            view was created
        patches_stage: the :class:`fiftyone.core.stages.ToPatches` stage that
            defines how the patches were extracted
        patches_dataset: the :class:`fiftyone.core.dataset.Dataset` that serves
            the patches in this view
    """

    _SAMPLE_CLS = PatchView

    def __init__(
        self, source_collection, patches_stage, patches_dataset, _stages=None
    ):
        super().__init__(
            source_collection, patches_stage, patches_dataset, _stages=_stages
        )

        self._patches_field = patches_stage.field

    @property
    def _label_fields(self):
        return [self._patches_field]

    @property
    def patches_field(self):
        """The field from which the patches in this view were extracted."""
        return self._patches_field


class EvaluationPatchesView(_PatchesView):
    """A :class:`fiftyone.core.view.DatasetView` containing evaluation patches
    from a :class:`fiftyone.core.dataset.Dataset`.

    Evalation patches views contain an ordered collection of evaluation
    examples, each of which contains the ground truth and/or predicted labels
    for a true positive, false positive, or false negative example from an
    evaluation run on the underlying dataset.

    Patches retrieved from patches views are returned as
    :class:`EvaluationPatchView` objects.

    Args:
        source_collection: the
            :class:`fiftyone.core.collections.SampleCollection` from which this
            view was created
        patches_stage: the :class:`fiftyone.core.stages.ToEvaluationPatches`
            stage that defines how the patches were extracted
        patches_dataset: the :class:`fiftyone.core.dataset.Dataset` that serves
            the patches in this view
    """

    _SAMPLE_CLS = EvaluationPatchView

    def __init__(
        self, source_collection, patches_stage, patches_dataset, _stages=None
    ):
        super().__init__(
            source_collection, patches_stage, patches_dataset, _stages=_stages
        )

        eval_key = patches_stage.eval_key
        eval_info = source_collection.get_evaluation_info(eval_key)
        self._gt_field = eval_info.config.gt_field
        self._pred_field = eval_info.config.pred_field

    @property
    def _label_fields(self):
        return [self._gt_field, self._pred_field]

    @property
    def gt_field(self):
        """The ground truth field for the evaluation patches in this view."""
        return self._gt_field

    @property
    def pred_field(self):
        """The predictions field for the evaluation patches in this view."""
        return self._pred_field

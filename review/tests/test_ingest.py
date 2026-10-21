import io

import numpy as np
import pytest
from PIL import Image as PILImage
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from review.services.ingest import UploadRejected, process_upload

from .conftest import png_bytes


def dicom_bytes(burned_in=None, monochrome1=False, frames=1):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientName = "Huber^Margarete"
    ds.PatientID = "4711"
    ds.PatientBirthDate = "19580314"
    ds.InstitutionName = "Some Hospital"
    ds.Modality = "CR"
    ds.BodyPartExamined = "CHEST"
    ds.Rows, ds.Columns = 32, 48
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME1" if monochrome1 else "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    ds.RescaleSlope, ds.RescaleIntercept = 1, 0
    ds.PixelSpacing = [0.2, 0.2]
    if burned_in:
        ds.BurnedInAnnotation = burned_in
    pixels = np.tile(np.linspace(0, 4000, 48, dtype=np.uint16), (32, 1))
    if frames > 1:
        ds.NumberOfFrames = frames
        pixels = np.stack([pixels] * frames)
    ds.PixelData = pixels.tobytes()
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def test_dicom_keeps_only_whitelisted_attributes():
    p = process_upload(dicom_bytes(), "x.dcm")
    assert p.source_format == "dicom"
    assert (p.width, p.height) == (48, 32)
    assert p.metadata["Modality"] == "CR"
    assert p.metadata["PixelSpacing"] == [0.2, 0.2]
    stored = repr(p.metadata) + p.png.read().decode("latin-1")
    for identifying in ("Huber", "4711", "19580314", "Some Hospital"):
        assert identifying not in stored


def test_dicom_is_windowed_to_full_8bit_range():
    p = process_upload(dicom_bytes(), "x.dcm")
    arr = np.array(PILImage.open(p.png))
    assert arr.min() == 0 and arr.max() == 255
    assert arr[0, 0] < arr[0, -1]  # dark left, bright right


def test_monochrome1_is_inverted():
    arr = np.array(PILImage.open(process_upload(dicom_bytes(monochrome1=True), "x.dcm").png))
    assert arr[0, 0] > arr[0, -1]


def test_multiframe_takes_one_frame():
    p = process_upload(dicom_bytes(frames=5), "x.dcm")
    assert (p.width, p.height) == (48, 32)


def test_burned_in_annotation_is_refused():
    with pytest.raises(UploadRejected, match="burned-in"):
        process_upload(dicom_bytes(burned_in="YES"), "x.dcm")


def test_png_is_reencoded():
    p = process_upload(png_bytes(), "a.png")
    assert p.source_format == "png" and (p.width, p.height) == (200, 100)
    assert len(p.sha256) == 64


def test_jpeg_exif_is_dropped():
    img = PILImage.fromarray(np.zeros((20, 20, 3), dtype=np.uint8))
    exif = PILImage.Exif()
    exif[0x013B] = "Dr. Somebody"  # Artist
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    out = PILImage.open(process_upload(buf.getvalue(), "a.jpg").png)
    assert not out.getexif()


def test_garbage_is_rejected():
    with pytest.raises(UploadRejected):
        process_upload(b"definitely not an image", "x.bin")

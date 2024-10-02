# Suggestion: Open and run with PowerShell ISE

# Directory where your files are located
$directory = "\media"

# Variables
$familyName = "Cassidy Family"
$copyrightNotice = "Cassidy Family Photos ©"

# Loop through each file in the directory
Get-ChildItem -Path $directory | ForEach-Object {

    # Extract the filename without the extension
    $fileName = $_.BaseName

    # Remove read-only attribute if it exists
    if ($_.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
        $_.Attributes = $_.Attributes -bxor [System.IO.FileAttributes]::ReadOnly
        Write-Host "Removed read-only attribute from: $fileName"
    }

    # Use regex to extract the title and date from file name
    # Example file name: 2024-09-26T15.30.45 - photo title [tag1;tag2].jpg
    if ($fileName -match "^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2})\.([0-9]{2})\.([0-9]{2}) - (.+?)(?: \[(.+)\])?$") {
        $year = $matches[1]
        $month = $matches[2]
        $day = $matches[3]
        $hour = $matches[4]
        $minute = $matches[5]
        $second = $matches[6]
        $title = $matches[7].Trim()
        $tags = $matches[8]  # Tags are optional

        # Build the date and time in the correct format for ExifTool
        $date = "${year}:${month}:${day} ${hour}:${minute}:${second}"
        $time = "${hour}:${minute}:${second}"

        # Validate fields
        if ($title -match "[\[\]]") {
            Write-Host "File Name Format Error: $fileName"
            exit
        }

        Write-Host "Processing file: $fileName"

        # Build tag values
        if ($tags) {
            $tags = $tags -replace "_", "/"
        }

        # Use ExifTool to set the various date fields and metadata
        $exifCommand = @(
            '-overwrite_original',

            "-DateTimeOriginal=$date",
            "-DateCreated=$date",
            "-CreateDate=$date",
            "-CreationDate=$date",
            "-ModifyDate=$date",
            "-MetadataDate=$date",
            "-MediaCreateDate=$date",
            "-MediaModifyDate=$date",
            "-TrackCreateDate=$date",
            "-TrackModifyDate=$date",
            "-FileModifyDate=$date",
            "-FileCreateDate=$date",
            "-TimeCreated=$time",
            "-ContentCreateDate#=$year",
            "-DigitalCreationDate=$date",
            "-DigitalCreationTime=${hour}:${minute}:${second}",

            "-Title=$title",
            "-Subject=",
            "-XPSubject=",
            "-By-line=",
            "-Caption-Abstract=",
            "-ImageDescription=",
            "-Description=",
            "-Subtitle=",
            "-XPComment=",
            "-URL=",

            "-Rating=",
            "-RatingPercent=",
            "-SharedUserRating=",

            "-Category="
            "-Microsoft:Category=$tags",
            "-Keywords=$tags",
            "-XPKeywords="

            "-Author=$familyName",
            "-XPAuthor=$familyName",
            "-Creator=$familyName",
            "-Artist=$familyName", 

            "-CreatorCity=",
            "-CreatorCountry=",
            "-CreatorPostalCode=",
            "-CreatorRegion=",
            "-CreatorWorkEmail=",
            "-CreatorWorkURL=",

            "-Copyright=$copyrightNotice",
            "-CopyrightNotice="
            "-Rights=",
            "-UsageTerms=",
            "-WebStatement=",
            "-Marked=",

            "-DerivedFromDocumentID=",
            "-DerivedFromOriginalDocumentID=",
            "-OriginalDocumentID=",
            "-DocumentID=",
            "-HistoryAction=",
            "-HistoryChanged=",
            "-HistoryInstanceID=",
            "-HistoryParameters=",
            "-HistorySoftwareAgent=",
            "-HistoryWhen=",
            "-InstanceID=",

            "-RawFileName=$fileName"
        )

        & exiftool @exifCommand "$($_.FullName)"
    }
    else {
        Write-Host "File Name Format Error: $$fileName"
        exit
    }
}

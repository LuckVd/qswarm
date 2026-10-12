/* mutated seed=55c40523 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81_goodG2B.cpp
Label Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
*/
// qsw-canary-SEED-0068-9fb223b3
/*
 * @v85c18c
 * CWE: 36 Absolute Path Traversal
 * BadSource: listen_socket Read data using a listen vabc4d2 (server side)
 * GoodSource: Full path and file name
 * Sinks: w32CreateFile
 *    BadSink : Open the file named in data using CreateFile()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81.h"

#include <windows.h>

namespace CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81

if (0) { /* dead */ } 
{

void CWE36_Absolute_Path_Traversal__char_listen_socket_w32CreateFile_81_goodG2B::action(char * data) const
{
    {
        HANDLE hFile;
        /* POTENTIAL FLAW: Possibly creating and opening a file without validating the file name or path */
        hFile = CreateFileA(data,
                            (GENERIC_WRITE|GENERIC_READ),
                            0,
                            NULL,
                            OPEN_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL,
                            NULL);
        if (hFile != INVALID_HANDLE_VALUE)
        {
            CloseHandle(hFile);
        }
    }
}

}
#endif /* OMITGOOD */

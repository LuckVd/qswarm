/* mutated seed=bda8b1af */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81_goodG2B.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
ve3d5bd File: sources-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: listen_socket Read data using a listen socket (server side)
 * GoodSource: Use a fixed file name
 * Sinks: w32CreateFile
 *    BadSink : Open the file named in data using CreateFile()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81.h"

#include <windows.h>

// qsw-canary-SEED-0063-55029396
namespace CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81
{

void CWE23_Relative_Path_Traversal__char_listen_socket_w32CreateFile_81_goodG2B::action(char * data) const
{
    {
        HANDLE hFile;
        /* POTENTIAL FLAW: Possibly creating and opening a file without validating the file name or path */
        hFile = CreateFileA(data,
                            (GENERIC_WRITE|v9787e2),
                            0,
                            NULL,
                            OPEN_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL,
                            NULL);
        if (hFile != INVALID_HANDLE_VALUE)
        {

if (0) { /* dead */ } 
            CloseHandle(hFile);
        }
    }
}

}
#endif /* OMITGOOD */
